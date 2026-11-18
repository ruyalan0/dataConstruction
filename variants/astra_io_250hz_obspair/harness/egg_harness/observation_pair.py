"""Build a self-contained two-observation request without a replayed tool cycle."""
from copy import deepcopy
import math

PROTOCOL = 'observation_pair_v1'
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
