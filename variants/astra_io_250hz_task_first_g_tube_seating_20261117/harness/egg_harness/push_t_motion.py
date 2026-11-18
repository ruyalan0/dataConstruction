"""Task-scoped reference selection and measured-state home-return estimates."""
from copy import deepcopy
import math
import os

import numpy as np

from .clocks import PHYSICS_HZ
from .interpolation_profiles import selected_profile

ARMS = ('left', 'right')
STAGE_PROFILES = {
    'initial_reach': 'linear',
    'coarse_push': 'linear',
    'manipulation': 'quintic',
    'home_return': 'linear',
}


def enabled(task=None, profile=None):
    task = os.environ.get('ASTRA_CONTROLLER_TASK') if task is None else task
    profile = selected_profile() if profile is None else profile
    return task == 'push_T' and profile == 'quintic'


def stage_settings(settings, stage):
    if not isinstance(stage, str) or stage not in STAGE_PROFILES:
        raise ValueError('push_T requires motion_stage: initial_reach, coarse_push, manipulation or home_return')
    result = dict(settings)
    result.update(interpolation_profile=STAGE_PROFILES[stage],
                  reference_velocity_limit_enforced=True,
                  reference_acceleration_limit_enforced=STAGE_PROFILES[stage] == 'quintic')
    return result


def prepare_motion(env, action, settings):
    if not enabled():
        return settings, None
    stage = action['motion_stage']
    effective = stage_settings(settings, stage)
    if stage != 'manipulation':
        native = getattr(env, 'native', env)
        finished = getattr(native, '_push_t_initial_reach_finished', set())
        for arm in action['arms']:
            if stage == 'initial_reach' and arm in finished:
                raise ValueError(arm + ': initial_reach cannot be reused after manipulation or home return')
    return effective, stage


def commit_motion(env, action, stage):
    if stage is None or stage == 'initial_reach':
        return
    native = getattr(env, 'native', env)
    finished = set(getattr(native, '_push_t_initial_reach_finished', set()))
    finished.update(action['arms'])
    native._push_t_initial_reach_finished = finished


def linear_return_estimate(distance, settings):
    if not math.isfinite(distance) or distance < 0:
        raise ValueError('Home distance must be finite and nonnegative')
    if distance <= settings['joint_tolerance_rad']:
        return dict(reference_and_settle_seconds=0., model_calls=0)
    settle_ticks = math.ceil(settings['settle_window_seconds'] * PHYSICS_HZ)
    available_ticks = math.floor(settings['max_motion_seconds'] * PHYSICS_HZ + 1e-9) - settle_ticks
    capacity = settings['joint_velocity_limit_rad_s'] * available_ticks / PHYSICS_HZ
    calls = max(1, math.ceil(distance / capacity - 1e-12))
    last_distance = max(0., distance - (calls - 1) * capacity)
    last_ticks = math.ceil(last_distance / settings['joint_velocity_limit_rad_s'] * PHYSICS_HZ - 1e-9)
    ticks = (calls - 1) * available_ticks + last_ticks + calls * settle_ticks
    return dict(reference_and_settle_seconds=ticks / PHYSICS_HZ, model_calls=calls)


def motion_policy(state, home_joints, settings):
    distances = {}
    for arm in ARMS:
        current = np.asarray(state[arm + '_arm_joint_state'], dtype=float)
        home = np.asarray(home_joints[arm], dtype=float)
        if current.shape != (6,) or home.shape != (6,) or not np.isfinite(current).all() or not np.isfinite(home).all():
            raise ValueError('Home budget requires finite measured and supplied home joints for both arms')
        distances[arm] = float(np.max(np.abs(current - home)))
    per_arm = {arm: linear_return_estimate(distance, settings) for arm, distance in distances.items()}
    simultaneous = linear_return_estimate(max(distances.values()), settings)
    sequential_seconds = sum(record['reference_and_settle_seconds'] for record in per_arm.values())
    sequential_calls = sum(record['model_calls'] for record in per_arm.values())
    clearance_seconds = 2 * settings['max_motion_seconds']
    completion_seconds = settings['hold_observation_seconds']
    return dict(
        stage_profiles=deepcopy(STAGE_PROFILES),
        linear_requires='Use coarse_push for short horizontal translation pushes while T-to-target planar '
                        'distance is greater than 0.0200 m. At or below 0.0200 m latch fine alignment in '
                        'memory and use manipulation/quintic for all later contact pushes, including '
                        'orientation-only correction. Initial free-space reach and separated final home '
                        'return remain linear. Use quintic for contact acquisition, unloading and withdrawal. '
                        'Closed empty fingers are allowed. '
                        'Inspect the entire swept path; gripper opening cannot prove separation.',
        per_goal_settling_unchanged=True,
        home_return_budget=dict(
            source='current measured joints to observation.home_joints; linear reference plus per-segment settling',
            max_joint_distance_rad=distances,
            per_arm=per_arm,
            simultaneous=simultaneous,
            sequential_reference_and_settle_seconds=sequential_seconds,
            sequential_model_calls=sequential_calls,
            withdrawal_and_clearance_allowance_seconds=clearance_seconds,
            completion_observation_seconds=completion_seconds,
            suggested_reserve_seconds=sequential_seconds + clearance_seconds + completion_seconds,
            suggested_reserve_model_calls=sequential_calls + 3,
            limitations='Nominal timing, not a clearance or reachability guarantee. Two bounded calls are allowed '
                        'for quintic unloading/separation; add time and calls for every extra clearance waypoint, retry or '
                        'tracking delay. Simultaneous return is allowed only when both swept paths are clear.'))
