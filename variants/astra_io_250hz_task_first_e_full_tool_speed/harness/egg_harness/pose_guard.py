"""Preflight X5 goals with static USD geometry, never with simulated contacts.

Convex bodies use their authored collision hull. For convexDecomposition bodies,
only original surface vertices strictly inside another authored convex hull are
positive overlap evidence. No whole concave-body hull is treated as solid.
This sampled screen is deliberately not a collision-free-path certificate.
"""
from functools import lru_cache
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linprog
from scipy.spatial.transform import Rotation

from .kinematics import pose_matrix

ARMS = ('left', 'right')
CONFIG = Path(__file__).resolve().parents[1] / 'configs/x5-collision-geometry.json'
SCOPE = ('X5 base_link and link1..6 self/inter-arm geometry; excludes gripper, '
         'cameras, carried objects and environment; sampled sweep, not PhysX contact evidence')
DEPTH_TOLERANCE = 1e-4
RECOVERY_TOLERANCE = 1e-5
SAMPLE_DELTA = .02


class PoseValidationError(ValueError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def checked_joints(model, arm, value):
    """Keep arm/index/angle/limits when the existing range validator rejects."""
    try:
        return model.joints_array(value).copy()
    except (ValueError, TypeError) as error:
        violations = []
        try:
            q = np.asarray(value, dtype=float)
            if q.shape == (6,) and np.isfinite(q).all():
                for i in np.flatnonzero((q < model.lower) | (q > model.upper)):
                    violations.append(dict(code='joint_limit', arm=arm, joint_index_1_based=int(i + 1),
                        requested_rad=float(q[i]), limits_rad=[float(model.lower[i]), float(model.upper[i])]))
        except (ValueError, TypeError):
            pass
        if not violations:
            violations = [dict(code='invalid_joint_vector', arm=arm, detail=str(error))]
        raise PoseValidationError(str(error), dict(accepted=False, stage='joint_limits',
            scope='Declared joint ranges and finite six-angle vectors', violations=violations,
            recovery_hint='Revise the reported arm/joints within their limits; neither arm executed.')) from error


class PoseGuard:
    def __init__(self, config=CONFIG):
        config = Path(config)
        self.record = json.loads(config.read_text())
        if self.record['schema'] != 'x5_usd_collision_geometry_v1':
            raise ValueError('Unknown collision geometry schema')
        data = config.with_suffix('.npz')
        if hashlib.sha256(data.read_bytes()).hexdigest() != self.record['geometry_sha256']:
            raise ValueError('X5 collision geometry checksum mismatch')
        self.joints = []
        for row in self.record['joints']:
            self.joints.append({**row, 'before': np.asarray(row['before']),
                'inverse_after': np.linalg.inv(row['after']), 'axis_vector': np.eye(3)['XYZ'.index(row['axis'])]})
        self.ignored = {frozenset(pair) for pair in self.record['ignored_self_pairs']}
        self.shapes = []
        with np.load(data, allow_pickle=False) as arrays:
            for row in self.record['shapes']:
                key = row['key']
                self.shapes.append({**row, 'points': arrays[key + '_points'],
                    'hull': arrays[key + '_hull'], 'planes': arrays[key + '_planes']})
        self.metadata = dict(scope=SCOPE, geometry_sha256=self.record['geometry_sha256'],
            sweep_max_joint_increment_rad=SAMPLE_DELTA, overlap_tolerance_m=DEPTH_TOLERANCE,
            limitations=['Finite sampling can miss between-sample collisions',
                        'Concave-body checks can miss intersections without an enclosed mesh vertex',
                        'An accepted goal is not a physical reachability or clearance guarantee'])

    def frames(self, joints):
        frames = {'base_link': np.eye(4)}
        for row, angle in zip(self.joints, joints):
            change = np.eye(4)
            change[:3, :3] = Rotation.from_rotvec(row['axis_vector'] * angle).as_matrix()
            frames[row['child']] = frames[row['parent']] @ row['before'] @ change @ row['inverse_after']
        return frames

    def _world(self, joints, bases):
        result = []
        for arm in ARMS:
            frames = self.frames(joints[arm])
            for shape in self.shapes:
                transform = bases[arm] @ frames[shape['body']]
                hull = shape['hull'] @ transform[:3, :3].T + transform[:3, 3]
                planes = shape['planes']
                normals = planes[:, :3] @ transform[:3, :3].T
                result.append(dict(arm=arm, shape=shape, transform=transform, hull=hull,
                    low=hull.min(0), high=hull.max(0), normals=normals,
                    offsets=planes[:, 3] - normals @ transform[:3, 3]))
        return result

    @staticmethod
    def _vertex_margin(source, target):
        if target['shape']['approximation'] != 'convexHull':
            return 0.
        # Cull in the source body's frame before evaluating hull half-spaces.
        transform = source['transform']
        local_hull = (target['hull'] - transform[:3, 3]) @ transform[:3, :3]
        points = source['shape']['points']
        mask = np.all((points >= local_hull.min(0)) & (points <= local_hull.max(0)), axis=1)
        points = points[mask]
        deepest = 0.
        for first in range(0, len(points), 256):
            world = points[first:first + 256] @ transform[:3, :3].T + transform[:3, 3]
            signed = np.max(world @ target['normals'].T + target['offsets'], axis=1)
            deepest = max(deepest, float(-signed.min()))
        return deepest

    def _overlaps(self, joints, bases):
        hits = {}
        for first, second in itertools.combinations(self._world(joints, bases), 2):
            a, b = first['shape'], second['shape']
            if first['arm'] == second['arm'] and (a['body'] == b['body'] or
                    frozenset((a['body'], b['body'])) in self.ignored):
                continue
            if np.any(np.minimum(first['high'], second['high']) - np.maximum(first['low'], second['low']) <= DEPTH_TOLERANCE):
                continue
            if a['approximation'] == b['approximation'] == 'convexHull':
                margin = self._convex_margin(first, second)
                metric = 'intersection_inscribed_radius_m'
            else:
                margin = max(self._vertex_margin(first, second), self._vertex_margin(second, first))
                metric = 'enclosed_surface_vertex_margin_m'
            if margin > DEPTH_TOLERANCE:
                key = (first['arm'], a['body'], second['arm'], b['body'])
                hits[key] = dict(code='self_collision_geometry_overlap' if first['arm'] == second['arm']
                    else 'inter_arm_geometry_overlap', arms=[first['arm'], second['arm']],
                    links=[a['body'], b['body']], geometry_margin_m=margin, margin_metric=metric)
        return hits

    def _convex_margin(self, first, second):
        """Use a signed radius so disjoint hulls have a feasible negative optimum."""
        normals = np.vstack([first['normals'], second['normals']])
        offsets = np.r_[first['offsets'], second['offsets']]
        matrix = np.c_[normals, np.ones(len(normals))]
        failures = []
        for method in ('highs', 'highs-ipm'):
            fit = linprog([0., 0., 0., -1.], A_ub=matrix, b_ub=-offsets,
                          bounds=[(None, None)] * 4, method=method)
            if (fit.success and np.shape(fit.x) == (4,) and np.isfinite(fit.x).all()
                    and np.max(matrix @ fit.x + offsets) <= 1e-7):
                return max(0., float(fit.x[3]))
            failures.append(dict(method=method, status=int(fit.status), message=str(fit.message)))
        violation = dict(code='collision_geometry_solver_failed',
                         arms=[first['arm'], second['arm']],
                         links=[first['shape']['body'], second['shape']['body']], attempts=failures)
        raise PoseValidationError('Collision geometry validation could not complete; neither arm executed',
            dict(accepted=False, stage='geometry_solver', **self.metadata, violations=[violation],
                 recovery_hint='Geometry validation is unavailable for this goal; no collision verdict was established.'))

    def validate(self, plan, state, trajectory):
        starts = {arm: np.asarray(plan[arm]['start']) for arm in ARMS}
        targets = {arm: np.asarray(plan[arm]['target']) for arm in ARMS}
        bases = {arm: pose_matrix(state[arm + '_ee_pose']) @ np.linalg.inv(self.frames(starts[arm])['link6'])
                 for arm in ARMS}
        report = dict(accepted=True, stage='preflight', **self.metadata,
                      requested_joint_goals={arm: targets[arm].tolist() for arm in ARMS}, violations=[])
        baseline = self._overlaps(starts, bases)
        if baseline:
            report['existing_overlaps'] = list(baseline.values())[:6]
            report['recovery_policy'] = 'Existing overlap may be held or reduced; no new or deeper overlap is accepted'

        def screen(joints, stage, fraction):
            for key, hit in self._overlaps(joints, bases).items():
                previous = baseline.get(key, {}).get('geometry_margin_m', 0.)
                if hit['geometry_margin_m'] <= previous + RECOVERY_TOLERANCE:
                    continue
                violation = dict(hit, sample_fraction=float(fraction))
                if key in baseline:
                    violation['starting_geometry_margin_m'] = previous
                message = (f"{hit['code']}: {key[0]} {key[1]} / {key[2]} {key[3]} at {stage}; "
                           'neither arm executed; change the joint goal or approach')
                raise PoseValidationError(message, dict(report, accepted=False, stage=stage,
                    violations=[violation], recovery_hint='Do not push farther into the reported pair. '
                    'Use a smaller retreat or a different posture; if occluded, use an empty idle wrist for observation.'))

        screen(targets, 'requested_goal', 1.)
        segment = {arm: np.asarray(trajectory.targets[arm]) for arm in ARMS}
        distance = max(float(np.max(np.abs(segment[arm] - starts[arm]))) for arm in ARMS)
        samples = max(1, int(np.ceil(distance / SAMPLE_DELTA)))
        for index in range(1, samples + 1):
            fraction = index / samples
            screen({arm: starts[arm] + fraction * (segment[arm] - starts[arm]) for arm in ARMS},
                   'segment_sweep', fraction)
        report['sweep_samples'] = samples
        return report


@lru_cache(maxsize=1)
def default_pose_guard():
    return PoseGuard()
