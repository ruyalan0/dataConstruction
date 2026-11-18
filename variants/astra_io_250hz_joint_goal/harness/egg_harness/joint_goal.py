"""Execute bounded, synchronized references without model-requested timing."""
from collections import deque
from copy import deepcopy
import json
import math
from numbers import Real
from pathlib import Path

import numpy as np

from .storage import check_configured_storage
from .clocks import PHYSICS_HZ, OBSERVATION_HZ, EXECUTION_MODE
from .visual_history import is_sample_tick
from .joint_trajectory import JointTrajectory

ARMS = ('left', 'right')


def controller_settings():
    path = Path(__file__).resolve().parents[1] / 'configs/joint-goal-controller.json'
    settings = json.loads(path.read_text())
    numeric = ('joint_tolerance_rad', 'settled_joint_velocity_rad_s',
               'settled_finger_range_m', 'settle_window_seconds',
               'stall_timeout_seconds', 'progress_threshold_rad',
               'max_motion_seconds', 'gripper_tolerance_m', 'held_gripper_change_guard_m',
               'joint_velocity_limit_rad_s', 'joint_acceleration_limit_rad_s2',
               'gripper_close_min_seconds', 'hold_observation_seconds')
    if set(settings) != {'mode', *numeric} or settings['mode'] != EXECUTION_MODE:
        raise ValueError('Unknown joint goal controller configuration')
    if any(isinstance(settings[k], bool) or not isinstance(settings[k], Real)
           or not math.isfinite(settings[k]) or settings[k] <= 0 for k in numeric):
        raise ValueError('Controller thresholds must be finite positive numbers')
    if not 1 / PHYSICS_HZ <= settings['settle_window_seconds'] < settings['stall_timeout_seconds'] <= settings['max_motion_seconds']:
        raise ValueError('Expected settle window < stall timeout <= motion timeout')
    if (math.ceil(settings['settle_window_seconds'] * PHYSICS_HZ) >=
            math.floor(settings['max_motion_seconds'] * PHYSICS_HZ + 1e-9)
            or any(not settings['settle_window_seconds'] <= settings[key] <= settings['max_motion_seconds']
                   for key in ('gripper_close_min_seconds', 'hold_observation_seconds'))):
        raise ValueError('Observation and gripper windows must fit the bounded execution interval')
    return settings


def compile_goal(model, state, action, grips):
    if not isinstance(action, dict) or set(action) != {'arms'}:
        raise ValueError('Joint goals require arms only; steps and settle are not accepted')
    arms = action['arms']
    if not isinstance(arms, dict) or set(arms) - set(ARMS):
        raise ValueError('Select left and/or right arm')
    plan = {}
    for arm in ARMS:
        spec = arms.get(arm, {})
        if not isinstance(spec, dict) or set(spec) - {'control', 'joints', 'gripper'}:
            raise ValueError('Unknown arm goal fields')
        if arm in arms and spec.get('control') != 'joint':
            raise ValueError('Only joint position goals are supported')
        start = model.joints_array(state[arm + '_arm_joint_state']).copy()
        target = model.joints_array(spec.get('joints', start)).copy()
        grip = spec.get('gripper', grips[arm])
        if isinstance(grip, bool) or not isinstance(grip, Real) or not math.isfinite(grip) or not 0 <= grip <= 1:
            raise ValueError('Gripper must be a finite opening in [0,1]')
        plan[arm] = dict(start=start, target=target, gripper=float(grip))
    return plan


def execute_control(env, output, command, setting, model, robot_state, observe, grips, plain):
    """One tool goal, one bounded reference segment, then public observation.

    Settled fingers never establish contact or retention. All waypoints, test
    lifts and recovery goals must still come from the model's control_arms call.
    """
    settings = controller_settings()
    start_step = int(env.take_action_cnt[0])
    start_submissions = env.goal_submissions
    feedback = dict(status='rejected_before_motion', reason=None, executed_steps=0, goal_submissions=0,
                    execution_mode=settings['mode'], controller=settings, controls={},
                    target_reached=False, tracking=[], goal_compilation={})

    def log(name, row):
        with (output / name).open('a') as stream:
            stream.write(json.dumps(row, default=plain, allow_nan=False) + '\n')

    def finish():
        feedback['executed_steps'] = int(env.take_action_cnt[0]) - start_step
        feedback['goal_submissions'] = env.goal_submissions - start_submissions
        feedback['actual_duration_seconds'] = feedback['executed_steps'] / PHYSICS_HZ
        log('control-feedback.jsonl', dict(command=command['id'], step=int(env.take_action_cnt[0]), **feedback))
        result = observe(command['id'])
        result['action_feedback'] = feedback
        return result

    state = robot_state()
    try:
        if setting != 'joint' or set(command) != {'id', 'op', 'action', 'phase'} or command['op'] != 'control_action':
            raise ValueError('Expected a joint-only control_action command')
        plan = compile_goal(model, state, command['action'], grips)
    except (ValueError, KeyError, TypeError) as error:
        feedback['reason'] = str(error)
        return finish()

    trajectory = JointTrajectory.from_plan(plan, settings)
    feedback['trajectory'] = trajectory.record()
    native = {}
    for arm, item in plan.items():
        native[arm + '_arm_joint_state'] = item['target']
        native[arm + '_ee_joint_state'] = np.array([item['gripper']])
    feedback['controls'] = {arm: 'joint' for arm in ARMS}
    feedback['requested_goals'] = {arm: dict(joints=item['target'].tolist(), gripper=item['gripper'])
                                   for arm, item in plan.items()}
    openings = {arm: np.asarray(state[arm + '_gripper_measured_joints'], dtype=float).copy() for arm in ARMS}
    watch_grip = {arm: grips[arm] == 0 and plan[arm]['gripper'] == 0 for arm in ARMS}
    window_ticks = math.ceil(settings['settle_window_seconds'] * PHYSICS_HZ)
    maximum_ticks = math.floor(settings['max_motion_seconds'] * PHYSICS_HZ + 1e-9)
    closing = any(plan[arm]['gripper'] < grips[arm] - 1e-9 for arm in ARMS)
    stationary = all(np.array_equal(plan[arm]['start'], plan[arm]['target']) for arm in ARMS)
    minimum_ticks = math.ceil((settings['gripper_close_min_seconds'] if closing else
                              settings['hold_observation_seconds'] if stationary else
                              settings['settle_window_seconds']) * PHYSICS_HZ)
    stall_ticks = math.ceil(settings['stall_timeout_seconds'] * PHYSICS_HZ)
    history = deque([deepcopy(state)], maxlen=window_ticks + 1)

    def total_error(measured):
        return sum(float(np.abs(np.asarray(measured[arm + '_arm_joint_state']) - plan[arm]['target']).sum())
                   for arm in ARMS)

    best_error, last_progress = total_error(state), 0
    feedback.update(status='observation_ready', reason='observation_interval_elapsed', retention_verified=False)
    for index in range(maximum_ticks):
        if env.is_episode_end():
            feedback.update(status='episode_ended', reason=None)
            break
        check_configured_storage()
        before = int(env.take_action_cnt[0])
        if index == 0:
            env.set_joint_goal(native, trajectory=trajectory)
            log('goals.jsonl', dict(command=command['id'], step=before,
                                   action=native, controls=feedback['controls'],
                                   execution_mode=settings['mode'], trajectory=trajectory.record()))
        reference = env.advance()
        if int(env.take_action_cnt[0]) != before + 1:
            raise RuntimeError('Native goal must advance exactly one physics tick')
        grips.update({arm: plan[arm]['gripper'] for arm in ARMS})
        log('actions.jsonl', dict(command=command['id'], step=before + 1,
                                 action=reference, controls=feedback['controls']))
        measured = robot_state()
        history.append(deepcopy(measured))
        rows, interrupted = [], False
        arm_reached = True
        for arm in ARMS:
            error = float(np.max(np.abs(np.asarray(measured[arm + '_arm_joint_state']) - plan[arm]['target'])))
            finger_change = float(np.max(np.abs(np.asarray(measured[arm + '_gripper_measured_joints']) - openings[arm])))
            stopped = watch_grip[arm] and finger_change > settings['held_gripper_change_guard_m']
            arm_reached = arm_reached and error <= settings['joint_tolerance_rad']
            reference_error = float(np.max(np.abs(np.asarray(measured[arm + '_arm_joint_state'])
                                                  - reference[arm + '_arm_joint_state'])))
            rows.append(dict(arm=arm, step=before + 1, joint_error_rad=error,
                             reference_joint_error_rad=reference_error,
                             opening_change_m=finger_change, stopped=bool(stopped)))
            if stopped:
                log('interruptions.jsonl', dict(command=command['id'], step=before + 1,
                                               arm=arm, reason='held_gripper_change', opening_change_m=finger_change))
                interrupted = True
        feedback['tracking'].extend(rows)
        feedback['joint_targets_reached'] = bool(arm_reached)
        feedback['final_joint_error_rad'] = {row['arm']: row['joint_error_rad'] for row in rows}
        feedback['segment_joint_error_rad'] = {
            arm: float(np.max(np.abs(np.asarray(measured[arm + '_arm_joint_state'])
                                     - trajectory.targets[arm]))) for arm in ARMS}
        if ((before + 1) % (PHYSICS_HZ // OBSERVATION_HZ) == 0
                or is_sample_tick(before + 1) or env.is_episode_end()):
            observe(f'step-{before + 1:04d}')
        if env.is_episode_end():
            feedback.update(status='episode_ended', reason=None)
            break
        if interrupted:
            feedback.update(status='tracking_or_gripper_change', reason='held_gripper_change')
            break
        ticks = index + 1
        error = total_error(measured)
        if error < best_error - settings['progress_threshold_rad']:
            best_error, last_progress = error, ticks
        if (ticks >= max(minimum_ticks, trajectory.duration_ticks + window_ticks)
                and len(history) == window_ticks + 1):
            window_seconds = window_ticks / PHYSICS_HZ
            settled = all(
                np.max(np.ptp([row[arm + '_arm_joint_state'] for row in history], axis=0))
                    <= settings['settled_joint_velocity_rad_s'] * window_seconds
                and np.max(np.ptp([row[arm + '_gripper_measured_joints'] for row in history], axis=0))
                    <= settings['settled_finger_range_m'] for arm in ARMS)
            segment_reached = all(error <= settings['joint_tolerance_rad']
                                  for error in feedback['segment_joint_error_rad'].values())
            if segment_reached and settled:
                if trajectory.goal_fraction < 1.:
                    feedback.update(status='observation_ready', reason='trajectory_segment_complete')
                    break
                grip_errors = {arm: float(np.max(np.abs(np.asarray(measured[arm + '_gripper_measured_joints'])
                                                         - plan[arm]['gripper'] * .044))) for arm in ARMS}
                grips_reached = all(error <= settings['gripper_tolerance_m'] for error in grip_errors.values())
                feedback.update(status='target_reached' if grips_reached else 'gripper_settled_before_target', reason=None,
                                target_reached=bool(grips_reached), gripper_error_m=grip_errors,
                                gripper_settled=True, retention_verified=False)
                break
        if not arm_reached and ticks - last_progress >= stall_ticks:
            feedback.update(status='motion_stalled', reason='no_measured_joint_progress')
            break
    if feedback['status'] in ('motion_stalled', 'motion_timeout'):
        log('interruptions.jsonl', dict(command=command['id'], step=int(env.take_action_cnt[0]),
                                       reason=feedback['status']))
    return finish()
