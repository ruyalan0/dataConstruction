"""Build a self-contained two-observation request without a replayed tool cycle."""
from copy import deepcopy
import json
import math
from pathlib import Path
from .input_contract import (compact_budget, compact_controller, compact_joint_semantics,
                             compact_motion, compact_task_requirements)
from .clocks import PHYSICS_HZ
from .control_timing import validate_timing_record
from .io_contract import round_numbers
from .privileged_scene import validate_privileged_scene
from .scene_feedback import validate_scene_feedback
from .arm_workspace import workspace_context
from .kinematics import Kinematics
from . import push_t_motion

WORKSPACE_MODEL = Kinematics(json.loads((Path(__file__).resolve().parents[1] / 'configs/x5-kinematics.json').read_text()))

PROTOCOL = 'observation_pair_joint_goal_privileged_v7'
CAMERAS = ('cam_head', 'cam_left_wrist', 'cam_right_wrist')
CURRENT_FIELDS = ('home', 'home_joints', 'joint_semantics', 'arm_posture',
                  'desired_gripper_commands', 'contact_estimates', 'last_motion', 'auxiliary_observation')
REQUIRED_STATE = tuple(arm + suffix for arm in ('left', 'right') for suffix in
                       ('_arm_joint_state', '_ee_pose', '_ee_joint_state', '_gripper_measured_joints'))


def _state(state):
    if not isinstance(state, dict) or not all(key in state for key in REQUIRED_STATE):
        raise ValueError('Observation must contain both measured arms, poses, finger joints and command echoes')
    for key in REQUIRED_STATE:
        value = state[key]
        expected = 6 if key.endswith('_arm_joint_state') else 7 if key.endswith('_ee_pose') else None
        if (not isinstance(value, list) or not value or (expected is not None and len(value) != expected)
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)):
            raise ValueError('Invalid public state field: ' + key)
    return deepcopy(state)


def snapshot(context, images):
    step = context.get('step')
    if type(step) is not int or step < 0:
        raise ValueError('Decision step must be a nonnegative control-tick count')
    if not all(name in images for name in CAMERAS):
        raise ValueError('Each observation needs all three camera views')
    result = dict(step=step, state=_state(context['state']),
                  cameras={name: str(images[name]) for name in CAMERAS})
    if 'arm_posture' in context:
        result['arm_posture'] = deepcopy(context['arm_posture'])
    return result


def build_pair(context, images, previous=None):
    """Use the preceding decision's pre-action measurements, not the prior physics tick.

    Current robot fields, a current privileged scene and one observation snapshot cross this boundary.
    No prior function arguments, complete result objects or tracking arrays survive;
    last_motion contains only the allowlisted outcome summary.
    """
    current = snapshot(context, images)
    if not isinstance(context.get('memory'), str):
        raise ValueError('Current observation requires explicit episode memory')
    timing = context['control_timing']
    validate_timing_record(timing)
    observation = dict(step=current['step'], state=current['state'], memory=context['memory'])
    observation.update({key: deepcopy(context[key]) for key in CURRENT_FIELDS if key in context})
    if 'joint_semantics' in observation:
        observation['joint_semantics'] = compact_joint_semantics(observation['joint_semantics'], current['state'])
    if 'last_motion' in observation:
        observation['last_motion'] = compact_motion(observation['last_motion'])
    history = dict(available=False, duration_ms=None, previous_observation=None)
    ordered_images = {}
    if previous is not None:
        prior = snapshot(previous, previous['cameras'])
        if prior['step'] > current['step']:
            raise ValueError('Previous decision observation cannot come from the future')
        history = dict(available=True, duration_ms=(current['step']-prior['step'])*1000/PHYSICS_HZ,
                       previous_observation={key: value for key, value in prior.items() if key != 'cameras'})
        ordered_images.update({'previous_' + name: prior['cameras'][name] for name in CAMERAS})
    ordered_images.update({'current_' + name: current['cameras'][name] for name in CAMERAS})
    payload = dict(input_protocol=PROTOCOL, instruction=context['instruction'],
                   history=history, observation=observation,
                   controller=compact_controller(timing),
                   budget=compact_budget(context['budget'], current['step'], PHYSICS_HZ),
                   privileged_scene=validate_privileged_scene(context.get('privileged_scene'), current['step']))
    if 'public_task_requirements' in context:
        payload['public_task_requirements'] = compact_task_requirements(context['public_task_requirements'], context['instruction'])
    if 'scene_feedback' in context:
        payload['scene_feedback'] = validate_scene_feedback(context['scene_feedback'], current['step'])
    if push_t_motion.enabled():
        observation['motion_policy'] = push_t_motion.motion_policy(
            current['state'], observation['home_joints'], timing['goal_controller'])
    # Derive at the shared serialization boundary so inference and exports
    # receive distances from these exact current measurements.
    observation['arm_workspace'] = workspace_context(WORKSPACE_MODEL, current['state'], payload['privileged_scene'], current['step'])
    # Validate/compact against original measurements first. Quantization is only
    # a model presentation boundary; the internal snapshot stays full precision.
    return round_numbers(payload), ordered_images, current


def build_visual_input(context, images, previous=None):
    """Merge sampled RGB with the two exact decision observations, chronologically.

    Sampled frames have no joint state. The existing history/observation fields
    retain the exact endpoint measurements and observation.memory.
    """
    payload, _, current = build_pair(context, images, previous)
    payload['visual_history'], ordered_images = build_visual_history(context.get('visual_history'), current, previous)
    return payload, ordered_images, current


def build_visual_history(window, current, previous=None):
    """Build the image timeline from captured evidence and exact endpoints."""
    from .visual_history import METADATA, TICK_MS, validate_window
    frames = validate_window(window, current['step'])
    entries = []
    for frame in frames:
        if frame['step'] < current['step']:
            entries.append(dict(step=frame['step'], roles=['history'], cameras=frame['cameras'],
                                sample_index=frame['sample_index'], target_time_ms=frame['target_time_ms']))
    if previous is not None:
        prior = snapshot(previous, previous['cameras'])
        match = next((row for row in entries if row['step'] == prior['step']), None)
        if match is None:
            entries.append(dict(step=prior['step'], roles=['previous_decision'], cameras=prior['cameras']))
        else:
            match['roles'].append('previous_decision')
            # Use the exact pre-action decision pictures when its time is sampled.
            match['cameras'] = prior['cameras']
    entries.append(dict(step=current['step'], roles=['current'], cameras=current['cameras']))
    entries.sort(key=lambda row: row['step'])  # Stable: previous precedes current even for zero motion.
    ordered_images = {}
    observations = []
    for index, entry in enumerate(entries):
        names = []
        for camera in CAMERAS:
            name = f'observation_{index:02d}_{camera}'
            ordered_images[name] = entry['cameras'][camera]
            names.append(name)
        row = {key: deepcopy(value) for key, value in entry.items() if key != 'cameras'}
        row.update(relative_time_ms=(entry['step'] - current['step']) * TICK_MS, images=names)
        if 'previous_decision' in entry['roles']:
            row['state_source'] = 'history.previous_observation.state'
        if 'current' in entry['roles']:
            row['state_source'] = 'observation.state'
        observations.append(row)
    return dict(METADATA, observations=observations), ordered_images
