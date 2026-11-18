"""Build a self-contained two-observation request without a replayed tool cycle."""
from copy import deepcopy
import math

PROTOCOL = 'visual_history_5s_4fps_v1'
CAMERAS = ('cam_head', 'cam_left_wrist', 'cam_right_wrist')
CURRENT_FIELDS = ('home', 'home_joints', 'joint_semantics', 'arm_posture',
                  'desired_gripper_commands', 'contact_estimates')
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

    Only public current fields and one observation snapshot cross this boundary.
    In particular no prior function arguments, results or tracking arrays survive.
    """
    current = snapshot(context, images)
    if not isinstance(context.get('memory'), str):
        raise ValueError('Current observation requires explicit episode memory')
    timing = context['control_timing']
    if (timing.get('protocol') != 'timed_joint_v1' or timing.get('action_hz') != 250
            or timing.get('observation_hz') != 25):
        raise ValueError('Observation pairs require timed 250 Hz control and a 25 Hz task clock')
    observation = dict(step=current['step'], state=current['state'], memory=context['memory'])
    observation.update({key: deepcopy(context[key]) for key in CURRENT_FIELDS if key in context})
    history = dict(available=False, duration_ms=None, previous_observation=None)
    ordered_images = {}
    if previous is not None:
        prior = snapshot(previous, previous['cameras'])
        if prior['step'] > current['step']:
            raise ValueError('Previous decision observation cannot come from the future')
        history = dict(available=True, duration_ms=(current['step']-prior['step'])*1000/250,
                       previous_observation={key: value for key, value in prior.items() if key != 'cameras'})
        ordered_images.update({'previous_' + name: prior['cameras'][name] for name in CAMERAS})
    ordered_images.update({'current_' + name: current['cameras'][name] for name in CAMERAS})
    payload = dict(input_protocol=PROTOCOL, instruction=context['instruction'],
                   history=history, observation=observation,
                   control_timing=deepcopy(timing), budget=deepcopy(context['budget']),
                   action_interface=context.get('action_interface', 'joint'))
    if 'public_task_requirements' in context:
        payload['public_task_requirements'] = deepcopy(context['public_task_requirements'])
    return payload, ordered_images, current


def build_visual_input(context, images, previous=None):
    """Merge sampled RGB with the two exact decision observations, chronologically.

    Sampled frames have no joint state. The existing history/observation fields
    retain the exact endpoint measurements and observation.memory.
    """
    from .visual_history import METADATA, TICK_MS, validate_window
    payload, _, current = build_pair(context, images, previous)
    frames = validate_window(context.get('visual_history'), current['step'])
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
    payload['visual_history'] = dict(METADATA, observations=observations)
    return payload, ordered_images, current
