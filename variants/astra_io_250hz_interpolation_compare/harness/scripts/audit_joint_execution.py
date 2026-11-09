"""Audit recorded multijoint execution from public commands, RGB records and URDF.

The checks independently derive FK and swept path-length bounds; they do not
use the runtime joint planner or claim collision freedom or task success.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from egg_harness.client import validate_joint_feedback

ARMS = ('left', 'right')

def require(condition, message):
    if not condition: raise ValueError(message)

def vector(value, size):
    array = np.asarray(value, dtype=float)
    require(array.shape == (size,) and np.isfinite(array).all(), 'Invalid recorded vector')
    return array

class Geometry:
    def __init__(self):
        self.defs = json.loads((Path(__file__).resolve().parents[1] / 'configs/x5-kinematics.json').read_text())['joints']
        self.origins = []
        for item in self.defs:
            origin = np.eye(4); origin[:3, 3] = item['xyz']
            origin[:3, :3] = Rotation.from_euler('xyz', item['rpy']).as_matrix()
            self.origins.append(origin)
        lengths = [np.linalg.norm(item['xyz']) for item in self.defs]
        self.radii = np.zeros((7, 6))
        for point in range(7):
            for joint in range(min(point, 6)):
                self.radii[point, joint] = (sum(lengths[joint + 1:min(point + 1, 6)]) + (.138 if point == 6 else 0)) * np.linalg.norm(self.defs[joint]['axis'])
    def chain(self, q, base=None):
        q = vector(q, 6); transform = np.eye(4) if base is None else base.copy(); points = []
        for index, item in enumerate(self.defs):
            require(item['lower'] - 1e-9 <= q[index] <= item['upper'] + 1e-9, 'Joint target outside URDF limits')
            transform = transform @ self.origins[index]
            points.append(transform[:3, 3].copy())
            rotation = np.eye(4); rotation[:3, :3] = Rotation.from_rotvec(np.asarray(item['axis']) * q[index]).as_matrix()
            transform = transform @ rotation
        points.append(transform[:3, 3] + transform[:3, :3] @ np.array([.138, 0, 0]))
        return transform, np.asarray(points)
    def pose(self, pose):
        pose = vector(pose, 7); require(abs(np.linalg.norm(pose[3:]) - 1) < .003, 'Invalid quaternion')
        transform = np.eye(4); transform[:3, 3] = pose[:3]
        transform[:3, :3] = Rotation.from_quat(pose[[4, 5, 6, 3]]).as_matrix()
        return transform
    def base(self, state, arm):
        return self.pose(state[arm + '_ee_pose']) @ np.linalg.inv(self.chain(state[arm + '_arm_joint_state'])[0])
    def check_segment(self, state, arm, q, box=None):
        before = vector(state[arm + '_arm_joint_state'], 6); q = vector(q, 6)
        delta = q - before; bounds = self.radii @ np.abs(delta)
        require(np.max(np.abs(delta)) <= .150000001, 'Native joint increment exceeded')
        require(bounds.max() <= .024000001, 'Conservative swept bound exceeded')
        base = self.base(state, arm); transform, points = self.chain(q, base)
        if box is not None:
            low, high = np.asarray(box, dtype=float)
            for fraction in (0., .25, .5, .75):
                sampled = self.chain(before + fraction * delta, base)[1]
                require(np.all(sampled - bounds[:, None] / 4 >= low + .03 - 1e-9) and np.all(sampled + bounds[:, None] / 4 <= high - .03 + 1e-9), 'Unfold segment leaves envelope')
            require(np.all(points >= low + .03 - 1e-9) and np.all(points <= high - .03 + 1e-9), 'Unfold endpoint leaves envelope')
        return transform, points, delta, bounds

def audit(episode, through):
    episode = Path(episode); require(type(through) is int and through >= 1, 'Positive prefix required')
    load = lambda path: json.loads(path.read_text())
    actions = [json.loads(line) for line in (episode / 'actions.jsonl').read_text().splitlines() if json.loads(line)['step'] <= through]
    require([row['step'] for row in actions] == list(range(1, through + 1)), 'Incomplete fixed action prefix')
    frames = {}
    def observation(step):
        if step not in frames:
            frames[step] = load(episode / 'frames' / (('initial' if step == 0 else f'step-{step:04d}') + '.json'))
        return frames[step]
    selected_rows = [row for row in actions if row.get('primitive') in ('joint_delta', 'unfold_arm')]
    require(selected_rows, 'No executed multijoint steps')
    groups = {}
    for row in selected_rows: groups.setdefault(row['command'], []).append(row)
    previews = {}
    for path in (episode / 'commands').glob('*.json'):
        command = load(path)
        if command['op'] not in ('preview_joint_delta', 'preview_unfold_arm'): continue
        response_path = episode / 'responses' / path.name
        if not response_path.exists(): continue
        response = load(response_path)
        if response['step'] > through: continue
        payload = load(episode / (command['id'] + '-joint-plan.json'))
        token = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
        require(response['joint_preview']['preview_id'] == token, 'Preview token does not bind plan/state')
        require(response['step'] == payload['step'], 'Preview took native steps')
        require(preview_command_fields(command) == payload['spec'], 'Saved preview spec differs from preview command')
        for key, value in payload['state'].items():
            require(np.allclose(response['state'][key], value, atol=1e-4, rtol=0), 'Preview response measured state mismatch')
        validate_joint_feedback(response, command['op'], command, payload['step'])
        require(response['joint_preview']['plan'] == payload['plan'], 'Preview response differs from saved plan')
        previews.setdefault(token, []).append((command, payload))
    geom = Geometry(); report = {}; used = set(); worst = 0.
    for identifier, rows in groups.items():
        command = load(episode / 'commands' / (identifier + '.json')); primitive = command['op']
        require(primitive in ('joint_delta', 'unfold_arm'), 'Primitive command mismatch')
        token = command['preview_id']; require(token in previews and token not in used, 'Missing or reused preview token'); used.add(token)
        preview_command, payload = previews[token][-1]
        require(payload == load(episode / (identifier + '-joint-plan.json')), 'Execution plan changed after preview')
        start = rows[0]['step'] - 1; initial = observation(start)['state']
        require(payload['step'] == start and payload['primitive'] == primitive, 'Stale execution preview')
        spec = {primitive: command[primitive]}
        if primitive == 'joint_delta': spec['steps'] = command['steps']
        require(payload['spec'] == spec and all(preview_command[key] == value for key, value in spec.items()), 'Command differs from preview spec')
        for arm in ARMS:
            for suffix in ('_arm_joint_state', '_ee_pose', '_gripper_measured_joints'):
                require(np.allclose(initial[arm + suffix], payload['state'][arm + suffix], atol=1e-4, rtol=0), 'Preview bound state differs from execution')
        if primitive == 'joint_delta':
            selected = set(command['joint_delta']); limit = command['steps']
            require(selected and not selected - set(ARMS) and type(limit) is int and 1 <= limit <= 6, 'Invalid joint command limit')
            masks = {arm: np.zeros(6, dtype=bool) for arm in ARMS}
            targets = {}
            for arm, changes in command['joint_delta'].items():
                require(isinstance(changes, dict) and changes, 'Empty selected joint map')
                target = vector(payload['state'][arm + '_arm_joint_state'], 6).copy()
                for name, value in changes.items():
                    require(name in tuple('joint%d' % n for n in range(1, 7)) and type(value) in (int, float) and np.isfinite(value) and abs(value) <= .6, 'Invalid joint increment spec')
                    index = int(name[-1]) - 1; masks[arm][index] = True; target[index] += value
                targets[arm] = target
            box = None
        else:
            spec = command['unfold_arm']; selected = {spec['arm']}; limit = spec['max_steps']; box = spec['clearance_box']
            require(spec['arm'] in ARMS and spec['reason'] in ('approach_blocked', 'descent_blocked', 'withdrawal_blocked') and type(limit) is int and 1 <= limit <= 24, 'Invalid unfold spec')
            array = np.asarray(box, dtype=float); require(array.shape == (2, 3) and np.isfinite(array).all() and np.all(array[1] - array[0] > .06) and np.all(array[1] - array[0] <= 1), 'Invalid unfold box')
            targets = {arm: vector(payload['plan']['targets'][arm], 6) for arm in selected}
            masks = {arm: np.abs(targets[arm] - initial[arm + '_arm_joint_state']) > 1e-9 if arm in selected else np.zeros(6, dtype=bool) for arm in ARMS}
        plan = payload['plan']; require(1 <= len(rows) <= plan['planned_steps'] <= limit, 'Execution exceeds command/plan limit')
        require([row['step'] for row in rows] == list(range(start + 1, start + len(rows) + 1)), 'Noncontiguous joint command')
        grips = {arm: actions[start - 1]['action'][arm + '_ee_joint_state'][0] if start else 1. for arm in ARMS}
        require(payload['grips'] == grips, 'Preview changed retained gripper intent')
        for arm in selected:
            require(grips[arm] >= .8 and min(initial[arm + '_gripper_measured_joints']) >= .035, 'Closed selected hand at start')
            require(np.allclose(plan['targets'][arm], targets[arm], atol=1e-9, rtol=0), 'Plan target differs from command')
        # Independently validate every zero-motion preview segment and geometry.
        simulated = dict(payload['state'])
        for frame in plan['path']:
            for arm in ARMS:
                transform, points, delta, bounds = geom.check_segment(simulated, arm, frame['joints'][arm], box if arm in selected else None)
                require(np.allclose(transform, geom.pose(frame['poses'][arm]), atol=1e-8, rtol=0), 'Preview FK mismatch')
                data = frame['geometry'][arm]
                require(np.allclose(data['origins_world_m'], points[:6]) and np.allclose(data['elbow_world_m'], points[2]) and np.allclose(data['wrist_world_m'], points[5]) and np.allclose(data['finger_proxy_world_m'], points[6]), 'Preview geometry mismatch')
                require(np.allclose(frame['bounds']['arms'][arm]['per_point_swept_bound_m'], bounds, atol=1e-9, rtol=0), 'Preview swept bound mismatch')
                require(np.allclose(np.asarray(frame['joints'][arm])[~masks[arm]], np.asarray(payload['state'][arm + '_arm_joint_state'])[~masks[arm]], atol=1e-9, rtol=0), 'Preview moved held joint')
                if arm in targets:
                    current = np.asarray(simulated[arm + '_arm_joint_state']); goal = targets[arm]
                    require(np.all(np.asarray(frame['joints'][arm]) >= np.minimum(current, goal) - 1e-9) and np.all(np.asarray(frame['joints'][arm]) <= np.maximum(current, goal) + 1e-9), 'Preview moves away from or beyond requested target')
            for arm in ARMS:
                simulated[arm + '_arm_joint_state'] = frame['joints'][arm]; simulated[arm + '_ee_pose'] = frame['poses'][arm]
        if primitive == 'unfold_arm':
            require(np.allclose(plan['path'][-1]['joints'][spec['arm']], targets[spec['arm']], atol=1e-9, rtol=0), 'Unfold target differs from last planned step')
        bad_steps = []; measured_tracking = []
        for row in rows:
            require(row['primitive'] == primitive and row['preview_id'] == token, 'Action primitive/token mismatch')
            before = observation(row['step'] - 1)['state']; after = observation(row['step'])['state']
            require(observation(row['step'])['cameras'], 'Missing per-step RGB record')
            for arm in ARMS:
                q = vector(row['action'][arm + '_arm_joint_state'], 6)
                require(np.allclose(q[~masks[arm]], np.asarray(initial[arm + '_arm_joint_state'])[~masks[arm]], atol=1e-9, rtol=0), 'Action moved held joint')
                if arm in targets:
                    current = np.asarray(before[arm + '_arm_joint_state']); goal = targets[arm]
                    require(np.all(q[masks[arm]] >= np.minimum(current, goal)[masks[arm]] - 1e-9) and np.all(q[masks[arm]] <= np.maximum(current, goal)[masks[arm]] + 1e-9), 'Action moves away from or beyond requested target')
                require(row['action'][arm + '_ee_joint_state'] == [grips[arm]] and row['cartesian_target'][arm + '_ee_joint_state'] == [grips[arm]], 'Action changed held gripper')
                transform, _, delta, bounds = geom.check_segment(before, arm, q, box if arm in selected else None)
                expected = geom.pose(initial[arm + '_ee_pose']) if arm not in selected else transform
                pose = vector(row['cartesian_target'][arm + '_ee_pose'], 7)
                require(np.allclose(expected, geom.pose(pose), atol=1e-8, rtol=0), 'Action FK/held pose mismatch')
                logged = row['joint_bounds']['arms'][arm]
                require(np.allclose(logged['delta_rad'], delta, atol=1e-9, rtol=0) and np.allclose(logged['per_point_swept_bound_m'], bounds, atol=1e-9, rtol=0) and abs(logged['swept_bound_m'] - bounds.max()) < 1e-9, 'Action bound record mismatch')
                worst = max(worst, float(bounds.max()))
                error = float(np.max(np.abs(vector(after[arm + '_arm_joint_state'], 6) - q)))
                wrist = float(np.linalg.norm(vector(after[arm + '_ee_pose'], 7)[:3] - pose[:3]))
                opening = np.asarray(after[arm + '_gripper_measured_joints']); drift = float(np.max(np.abs(opening - initial[arm + '_gripper_measured_joints'])))
                bad = error > .03 or wrist > .012 or drift > .005 or (arm in selected and opening.min() < .035)
                if bad: bad_steps.append(row['step'])
                measured_tracking.append({'arm': arm, 'step': row['step'], 'joint_error_rad': error, 'wrist_error_m': wrist, 'opening_change_m': drift, 'stopped': bool(bad)})
            require(not bad_steps or row is rows[-1], 'Execution continued after bad tracking')
        response_path = episode / 'responses' / (identifier + '.json')
        complete = response_path.exists() and load(response_path)['step'] <= through
        if complete:
            response = load(response_path); validate_joint_feedback(response, primitive, command, start)
            feedback = response['joint_control']
            require(response['step'] == rows[-1]['step'] and feedback['executed_steps'] == len(rows), 'Response step/count mismatch')
            for arm in ARMS:
                for suffix in ('_arm_joint_state', '_ee_pose', '_gripper_measured_joints'):
                    require(np.allclose(response['state'][arm + suffix], observation(response['step'])['state'][arm + suffix], atol=1e-4, rtol=0), 'Response differs from final measured state')
            require(feedback == load(episode / (identifier + '-joint-result.json')), 'Saved feedback differs from response')
            require(feedback['tracking'][:len(measured_tracking)] == measured_tracking, 'Tracking feedback differs from measured evidence')
            require(not bad_steps or feedback['status'] == 'tracking_or_gripper_change', 'Bad tracking lacks stop status')
            reached = all(np.max(np.abs(np.asarray(response['state'][arm + '_arm_joint_state']) - targets[arm])) < .001 for arm in selected)
            require(feedback['target_reached'] == bool(reached) and (feedback['status'] != 'target_reached' or reached), 'False target completion claim')
        report[identifier] = {'primitive': primitive, 'start_step': start, 'executed_steps': len(rows), 'complete_response_in_prefix': complete, 'bad_tracking_steps': sorted(set(bad_steps))}
    return {'through_step': through, 'joint_steps': [row['step'] for row in selected_rows], 'commands': report, 'max_independent_swept_bound_m': worst, 'prefix_sha256': hashlib.sha256(json.dumps(actions, sort_keys=True).encode()).hexdigest(), 'scope': 'Recorded public-state command/preview binding, independent URDF FK and conservative sweep, held joints/grips and tracking-stop checks; no collision, grasp, blocked-motion recovery or native qualification claim'}

def preview_command_fields(command):
    primitive = command['op'].removeprefix('preview_')
    result = {primitive: command[primitive]}
    if primitive == 'joint_delta': result['steps'] = command['steps']
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('episode', type=Path); parser.add_argument('--through', required=True, type=int); parser.add_argument('--output', required=True, type=Path); args = parser.parse_args()
    require(not args.output.exists(), 'New output required'); result = audit(args.episode, args.through)
    args.output.write_text(json.dumps(result, indent=2)); print(json.dumps(result))
