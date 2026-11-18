from collections import deque
from copy import deepcopy
import re

import numpy as np
from scipy.spatial import ConvexHull, QhullError
from scipy.spatial.transform import Rotation

from .privileged_scene import array, pose


SCHEMA = 'linear_scene_feedback_v1'
ARMS = ('left', 'right')


def transform(value):
    value = array(value, (7,))
    matrix = np.eye(4)
    matrix[:3, :3] = Rotation.from_quat(value[[4, 5, 6, 3]]).as_matrix()
    matrix[:3, 3] = value[:3]
    return matrix


def rotation_angle(matrix):
    return float(Rotation.from_matrix(matrix).magnitude())


def surface_geometry(obj, record):
    try:
        points = array(obj.sample_mesh_vertices()[0]).reshape(-1, 3)
        if not len(points):
            raise ValueError('No current mesh vertices')
        if record.get('bbox') and record['object_type'] == 'rigid':
            expected = array(record['bbox']['center_world'])
            if np.linalg.norm((points.min(axis=0) + points.max(axis=0)) / 2 - expected) > .03:
                raise ValueError('Current mesh transform disagrees with live object bounding box')
        points = points[::max(1, len(points) // 4096)]
        center = points.mean(axis=0)
        _, _, axes = np.linalg.svd(points - center, full_matrices=False)
        if axes.shape != (3, 3):
            raise ValueError('Insufficient mesh geometry')
        projections = (points - center) @ axes.T
        extents = np.ptp(projections, axis=0)
        horizontal = [index for index, axis in enumerate(axes) if abs(axis[2]) < .7]
        chosen = min(horizontal, key=lambda index: extents[index]) if horizontal else None
        result = dict(source='current_mesh_vertices', sampled_vertices=len(points),
                      min_world=points.min(axis=0).tolist(), max_world=points.max(axis=0).tolist(),
                      principal_axes_world=axes.tolist(), principal_extents_m=extents.tolist(),
                      grasp_candidate=None)
        if chosen is not None and record['object_type'] == 'rigid':
            result['grasp_candidate'] = dict(
                center_world=(center + ((projections.min(axis=0) + projections.max(axis=0)) / 2) @ axes).tolist(),
                closing_axis_world=axes[chosen].tolist(), width_m=float(extents[chosen]),
                source='mesh_principal_box_candidate', reachable=None,
                collision_free=None, force_closure_verified=False)
        return result
    except (AttributeError, ValueError, TypeError, RuntimeError, np.linalg.LinAlgError) as error:
        return dict(source='unavailable', reason=str(error), grasp_candidate=None)


def inside_basket(record, basket):
    if 'position_world' not in record or 'bbox' not in basket:
        return None
    corners = array(basket['bbox']['vertices_world']).reshape(-1, 3)
    position = array(record['position_world'])
    try:
        hull = ConvexHull(corners[:, :2])
    except (QhullError, ValueError):
        return None
    inside = bool(np.all(hull.equations[:, :2] @ position[:2] + hull.equations[:, 2] < 0))
    return bool(inside and corners[:, 2].min() < position[2] < corners[:, 2].max() + .01)


def classification_progress(scene, instruction, state):
    matched = re.fullmatch(
        r'Put (.+) objects into the left basket, (.+) objects into the middle basket, '
        r'and (.+) objects into the right basket, then reset the robot arm\.', instruction)
    if matched is None:
        return dict(available=False, reason='No supported category-to-basket instruction')
    objects = scene['objects']
    groups = []
    opened = all(min(state[arm + '_gripper_measured_joints']) >= .8 * .044 for arm in ARMS)
    for index, category in enumerate(matched.groups()):
        basket = objects.get(f'basket{index}')
        if basket is None:
            return dict(available=False, reason='Missing basket geometry')
        wanted = {label: value for label, value in objects.items() if value.get('category') == category}
        contained = {label: inside_basket(value, basket) for label, value in objects.items()
                     if value.get('object_type') == 'rigid'}
        placed = [label for label in wanted if contained.get(label) is True]
        missing = [label for label in wanted if contained.get(label) is not True]
        contaminants = [label for label, value in contained.items() if value is True and label not in wanted]
        unknown = [label for label in wanted if contained.get(label) is None]
        groups.append(dict(category=category, basket=f'basket{index}', total=len(wanted),
                           placed=placed, remaining=missing, contaminants=contaminants, unknown=unknown,
                           geometry_complete=bool(wanted) and not missing and not contaminants,
                           grippers_open=opened))
    return dict(available=True, source='current_object_geometry_and_instruction', categories=groups,
                evaluator_queried=False, support_verified=False)


class SceneFeedback:
    def __init__(self):
        self.history = deque()
        self.initial = {}
        self.metadata = {}
        self.last_motion_status = {}
        self.home = None

    def sample(self, env, state):
        step = int(env.take_action_cnt[0])
        if self.history and self.history[-1]['step'] == step:
            return
        if self.history and step < self.history[-1]['step']:
            raise ValueError('Scene feedback cannot read a past step')
        manager = env.scene_manager.layout_manager
        origin = array(env.scene_manager.env_origins[0], (3,))
        objects = {}
        for label in manager.get_labels_by_prefix(0, ''):
            instance = manager.get_instance_name(0, label)
            if instance not in self.metadata:
                self.metadata[instance] = manager.get_instance_metadata(env_idx=0, inst_name=instance) or {}
            if manager.instance_type_by_env[0].get(instance) not in ('rigid', 'garment'):
                continue
            try:
                position, quaternion = manager.get_instance_pose(env_idx=0, inst_name=instance, relative=True)
                value = pose(array(position, (3,)) + origin, quaternion)
                objects[label] = value
                self.initial.setdefault(label, deepcopy(value))
            except (ValueError, TypeError, AttributeError, RuntimeError):
                continue
        if self.home is None:
            self.home = {arm: array(state[arm + '_arm_joint_state']).copy() for arm in ARMS}
        self.history.append(dict(step=step, objects=objects, state=deepcopy(state)))
        while self.history and step - self.history[0]['step'] > 125:
            self.history.popleft()

    def movement(self, label, arm):
        records = [row for row in self.history if label in row['objects']]
        if not records:
            return dict(status='unavailable')
        latest = records[-1]
        relatives = []
        wrists = []
        for row in records:
            obj = row['objects'][label]
            world_object = transform([*obj['position_world'], *obj['quaternion_wxyz']])
            wrist = transform(row['state'][arm + '_ee_pose'])
            wrists.append(wrist)
            relatives.append(np.linalg.inv(wrist) @ world_object)
        duration = (records[-1]['step'] - records[0]['step']) / 250
        relative_translation = max(float(np.linalg.norm(value[:3, 3] - relatives[0][:3, 3])) for value in relatives)
        relative_rotation = max(rotation_angle(relatives[0][:3, :3].T @ value[:3, :3]) for value in relatives)
        wrist_translation = max(float(np.linalg.norm(value[:3, 3] - wrists[0][:3, 3])) for value in wrists)
        wrist_rotation = max(rotation_angle(wrists[0][:3, :3].T @ value[:3, :3]) for value in wrists)
        object_distance = float(np.linalg.norm(relatives[-1][:3, 3] - [.138, 0., 0.]))
        measured = latest['state'][arm + '_gripper_measured_joints']
        commanded = latest['state'].get(arm + '_ee_joint_state', [1.])[0]
        near_closed = object_distance < .12 and commanded < .8 and min(measured) < .0352
        moved = wrist_translation > .01 or wrist_rotation > .1
        status = 'uncertain'
        if not near_closed:
            status = 'not_near_closed_gripper'
        elif duration >= .08 and len(records) >= 3 and moved:
            if relative_translation < .004 and relative_rotation < .15:
                status = 'moves_with_gripper'
            elif relative_translation > .008 or relative_rotation > .25:
                status = 'relative_motion_detected'
        prior = self.last_motion_status.get((label, arm))
        self.last_motion_status[(label, arm)] = status
        result = dict(status=status, sample_count=len(records), window_seconds=duration,
                      position_in_wrist_frame=relatives[-1][:3, 3].tolist(),
                      distance_to_finger_reference_m=object_distance,
                      relative_translation_range_m=relative_translation,
                      relative_rotation_range_rad=relative_rotation,
                      wrist_translation_range_m=wrist_translation, wrist_rotation_range_rad=wrist_rotation,
                      possible_slip=prior == 'moves_with_gripper' and (relative_translation > .008 or relative_rotation > .25),
                      secure_grasp_verified=False)
        if duration > 0:
            result['object_velocity_world_m_s'] = (
                (array(latest['objects'][label]['position_world']) - array(records[0]['objects'][label]['position_world'])) / duration).tolist()
        return result

    def capture(self, env, scene, state, instruction):
        self.sample(env, state)
        step = int(env.take_action_cnt[0])
        if scene['step'] != step:
            raise ValueError('Scene feedback and RGB must have the same step')
        manager = env.scene_manager.layout_manager
        objects = {}
        for label, record in scene['objects'].items():
            if record['object_type'] not in ('rigid', 'garment'):
                continue
            instance = manager.get_instance_name(0, label)
            obj = manager.get_scene_object(0, instance)
            current = dict(geometry=surface_geometry(obj, record), motion={arm: self.movement(label, arm) for arm in ARMS})
            if label in self.initial and 'position_world' in record:
                current['height_change_from_initial_m'] = record['position_world'][2] - self.initial[label]['position_world'][2]
            current['contacts'] = dict(available=False, reason='Pairwise finger-object contact sensors are not configured')
            objects[label] = current
        result = dict(schema=SCHEMA, step=step, source='automatic_current_simulator_observation',
                      objects=objects, classification=classification_progress(scene, instruction, state),
                      home_joint_error_rad={arm: float(np.max(np.abs(array(state[arm + '_arm_joint_state']) - self.home[arm]))) for arm in ARMS},
                      limitations=['Geometric grasp candidates do not establish reachability or collision clearance',
                                   'Relative co-motion does not prove a secure grasp or absence of support',
                                   'Mesh summaries do not identify garment material landmarks or liquid flow'])
        if int(env.take_action_cnt[0]) != step:
            raise RuntimeError('Scene feedback capture advanced physics')
        return validate_scene_feedback(result, step)


def validate_scene_feedback(value, step):
    if not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('step') != step:
        raise ValueError('Invalid or stale scene feedback')
    def finite(item):
        if isinstance(item, dict):
            for child in item.values():
                finite(child)
        elif isinstance(item, list):
            for child in item:
                finite(child)
        elif isinstance(item, float) and not np.isfinite(item):
            raise ValueError('Nonfinite scene feedback')
    finite(value)
    return deepcopy(value)
