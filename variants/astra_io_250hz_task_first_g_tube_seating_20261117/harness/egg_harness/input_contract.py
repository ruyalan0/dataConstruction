"""Lossless presentation compaction for the joint-goal model input."""
from copy import deepcopy
import math

ARMS = ('left', 'right')
TASK_ADMIN_ROWS = {'Platform', 'Category', 'Data Source', 'Usage'}
TASK_SOURCE_FIELDS = {'url', 'fetched_at', 'sha256'}
MODEL_MOTION_FIELDS = ('status', 'reason', 'final_joint_error_rad', 'gripper_error_m', 'segment_joint_error_rad', 'pose_validation')
CONTROLLER_FIELDS = ('mode', 'joint_tolerance_rad', 'settle_window_seconds',
                     'stall_timeout_seconds', 'max_motion_seconds',
                     'held_gripper_change_guard_m', 'gripper_tolerance_m',
                     'joint_velocity_limit_rad_s', 'joint_acceleration_limit_rad_s2',
                     'gripper_close_min_seconds', 'hold_observation_seconds',
                     'interpolation_profile', 'reference_velocity_limit_enforced',
                     'reference_acceleration_limit_enforced')


def compact_joint_semantics(source, state):
    result = deepcopy(source)
    static_fields = ('topology_role', 'axis_joint_frame', 'limits_rad')
    arms = result['arms']
    for arm in ARMS:
        joints = arms[arm]
        if len(joints) != len(state[arm + '_arm_joint_state']):
            raise ValueError('Joint semantics do not match measured joints')
        for index, joint in enumerate(joints):
            if joint['angle_rad'] != state[arm + '_arm_joint_state'][index]:
                raise ValueError('Joint semantics contain stale measured angles')
            del joint['angle_rad']
            if joint['urdf_name'] == joint['control_name']:
                del joint['urdf_name']
    # Share definitions only when both arms actually have the same robot mapping.
    # Different definitions retain their original per-arm fields.
    pairs = list(zip(arms['left'], arms['right']))
    if all(left['control_name'] == right['control_name']
           and all(left[key] == right[key] for key in static_fields) for left, right in pairs):
        result['joint_definitions'] = {
            joint['control_name']: {key: deepcopy(joint[key]) for key in static_fields}
            for joint in arms['left']}
        for joints in arms.values():
            for joint in joints:
                for key in static_fields:
                    del joint[key]
    return result


def compact_task_requirements(requirements, instruction):
    result = {key: deepcopy(value) for key, value in requirements.items() if key not in TASK_SOURCE_FIELDS}
    rows = []
    for row in result.get('rows', []):
        if row[0] in TASK_ADMIN_ROWS:
            continue
        if row[0] == 'Instruction' and len(row) == 2 and row[1] == instruction:
            continue
        rows.append(row)
    if 'rows' in result:
        result['rows'] = rows
    return result


def compact_budget(budget, step, physics_hz):
    horizon = budget['task_horizon_seconds']
    calls = budget['model_calls_remaining']
    if (isinstance(horizon, bool) or not isinstance(horizon, (int, float))
            or not math.isfinite(horizon) or horizon <= 0 or type(calls) is not int or calls < 0):
        raise ValueError('Invalid simulation horizon or model call budget')
    remaining = horizon - step / physics_hz
    if remaining < -1e-9:
        raise ValueError('Observation exceeds the simulation horizon')
    return dict(remaining_simulation_seconds=max(0., remaining), model_calls_remaining=calls)


def compact_motion(feedback):
    # Outcome and errors are useful; elapsed time already appears in history.
    # Settled/target booleans are encoded by the documented status vocabulary.
    fields = MODEL_MOTION_FIELDS + (('motion_stage', 'trajectory', 'actual_duration_seconds')
                                   if 'motion_stage' in feedback else ())
    return {key: deepcopy(feedback[key]) for key in fields
            if key in feedback and feedback[key] is not None}


def compact_controller(timing):
    settings = timing['goal_controller']
    return {key: deepcopy(settings[key]) for key in CONTROLLER_FIELDS}


def strip_schema_titles(value):
    if isinstance(value, dict):
        return {key: strip_schema_titles(item) for key, item in value.items()
                if not (key == 'title' and isinstance(item, str))}
    if isinstance(value, list):
        return [strip_schema_titles(item) for item in value]
    return value
