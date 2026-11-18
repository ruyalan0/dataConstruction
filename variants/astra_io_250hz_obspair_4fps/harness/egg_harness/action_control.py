"""Bounded TCP/native-joint execution, qualified in the L20/L21 action ablation."""
import json
from numbers import Real
import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from .kinematics import pose_matrix
from .segments import compile_segment
from .storage import check_configured_storage
from .control_timing import MAX_ACTION_STEPS, TICKS_PER_TASK_STEP
from .visual_history import is_sample_tick

ARMS = ('left', 'right')
SETTINGS = {'tcp': {'tcp'}, 'joint': {'joint'}, 'both': {'tcp', 'joint'}}
DEFAULT_ACTION_INTERFACE = 'joint'


def action_interface(config):
    value = config.get('action_interface', DEFAULT_ACTION_INTERFACE)
    if not isinstance(value, str) or value not in SETTINGS:
        raise ValueError('action_interface must be joint, tcp or both')
    return value


def matrix_pose(matrix):
    return np.r_[matrix[:3, 3], Rotation.from_matrix(matrix[:3, :3]).as_quat()[[3, 0, 1, 2]]]


def compile_action(model, state, action, phase, setting, grips):
    if setting not in SETTINGS:
        raise ValueError('Unknown action interface')
    if phase not in ('fine', 'transit') or not isinstance(action, dict):
        raise ValueError('Expected fine/transit phase and action object')
    if set(action) - {'arms', 'steps', 'settle'} or not {'arms', 'steps'} <= set(action):
        raise ValueError('Action requires arms, steps and optional settle')
    steps, settle = action['steps'], action.get('settle', 0)
    if type(steps) is not int or not 1 <= steps <= MAX_ACTION_STEPS:
        raise ValueError('Duration must be 1..2500 control ticks (at most 10 seconds)')
    if type(settle) is not int or not 0 <= settle <= 2:
        raise ValueError('Settle must be 0..2')
    arms = action['arms']
    if not isinstance(arms, dict) or set(arms) - set(ARMS):
        raise ValueError('Select left and/or right; empty arms holds')
    plan = {'steps': steps, 'settle': settle, 'arms': {}}
    for arm, spec in arms.items():
        if not isinstance(spec, dict) or spec.get('control') not in SETTINGS[setting]:
            raise ValueError('Arm control is not enabled in setting ' + setting)
        control = spec['control']
        fields = {'control', 'gripper'} | ({'toward'} if control == 'tcp' else {'joints'})
        if set(spec) - fields:
            raise ValueError('Unknown arm fields for ' + control)
        grip = spec.get('gripper', grips[arm])
        if isinstance(grip, bool) or not isinstance(grip, Real) or not np.isfinite(grip) or not 0 <= grip <= 1:
            raise ValueError('Gripper must be a finite continuous opening in [0,1]')
        item = {'control': control, 'gripper': float(grip)}
        if control == 'tcp':
            start = np.asarray(state[arm + '_ee_pose'], float)
            target = start.copy()
            item['goal_compilation'] = None
            if 'toward' in spec:
                segment, goal = compile_segment({'arm': arm, 'toward': spec['toward'], 'steps': steps}, phase, {'state': state})
                target = np.r_[segment['position'], segment['quaternion']]
                item['goal_compilation'] = goal
            pose_matrix(target)
            item.update(start=start, target=target)
        else:
            start = model.joints_array(state[arm + '_arm_joint_state'])
            target = model.joints_array(spec.get('joints', start))
            if np.max(np.abs(target - start)) > .15 * steps + 1e-9:
                raise ValueError('Joint target needs a shorter prefix or more steps: max 0.15 rad/joint/step')
            item.update(start=start.copy(), target=target.copy())
        plan['arms'][arm] = item
    return plan


def next_native_action(model, state, plan, index, grips):
    """Resolve both arms before executing either. No alternate IK seeds or recovery."""
    fraction = min((index + 1) / plan['steps'], 1.)
    native, cartesian = {}, {}
    for arm in ARMS:
        current = model.joints_array(state[arm + '_arm_joint_state'])
        pose = np.asarray(state[arm + '_ee_pose'], float)
        item = plan['arms'].get(arm)
        grip = grips[arm] if item is None else item['gripper']
        if item is None:
            solution, target_pose = current.copy(), pose.copy()
        elif item['control'] == 'joint':
            solution = item['start'] + fraction * (item['target'] - item['start'])
            if np.max(np.abs(solution - current)) > .150000001:
                raise ValueError(arm + ': joint interpolation exceeds measured-state 0.15 rad bound')
            base = model.observed_base(current, pose)
            target_pose = matrix_pose(base @ model.forward(solution))
        else:
            start, target = item['start'], item['target']
            rotations = Rotation.from_quat(np.array([start[3:], target[3:]])[:, [1, 2, 3, 0]])
            target_pose = np.r_[start[:3] + fraction * (target[:3] - start[:3]),
                                Slerp([0, 1], rotations)(fraction).as_quat()[[3, 0, 1, 2]]]
            solution = (current.copy() if np.allclose(target_pose, pose, atol=1e-7)
                        else model.solve_local(current, pose, target_pose))
        native[arm + '_arm_joint_state'] = solution
        native[arm + '_ee_joint_state'] = np.array([grip])
        cartesian[arm + '_ee_pose'] = target_pose
        cartesian[arm + '_ee_joint_state'] = np.array([grip])
    return native, cartesian


def execute_control(env, output, command, setting, model, robot_state, observe, grips, plain):
    """One bounded segment, public feedback, and durable per-native-step records."""
    start_step = int(env.take_action_cnt[0])
    feedback = {'status': 'completed', 'reason': None, 'executed_steps': 0,
                'controls': {}, 'tracking': [], 'goal_compilation': {}}
    def log(name, row):
        with (output / name).open('a') as stream:
            stream.write(json.dumps(row, default=plain, allow_nan=False) + '\n')
    def finish():
        feedback['executed_steps'] = int(env.take_action_cnt[0]) - start_step
        log('control-feedback.jsonl', {'command': command['id'], 'step': int(env.take_action_cnt[0]), **feedback})
        result = observe(command['id'])
        result['action_feedback'] = feedback
        return result
    state = robot_state()
    try:
        if set(command) != {'id', 'op', 'action', 'phase'}:
            raise ValueError('Unknown control command fields')
        plan = compile_action(model, state, command['action'], command['phase'], setting, grips)
    except (ValueError, KeyError, TypeError) as error:
        feedback.update(status='rejected_before_motion', reason=str(error))
        return finish()
    feedback['controls'] = {a: item['control'] for a, item in plan['arms'].items()}
    feedback['goal_compilation'] = {a: item['goal_compilation'] for a, item in plan['arms'].items() if item['control'] == 'tcp'}
    openings = {a: float(state[a + '_gripper_measured_joints'][0]) for a in ARMS}
    watch_grip = {a: grips[a] == 0 and plan['arms'].get(a, {}).get('gripper', grips[a]) == 0 for a in ARMS}
    for index in range(plan['steps'] + plan['settle']):
        if env.is_episode_end():
            feedback['status'] = 'episode_ended'
            break
        check_configured_storage()
        state = robot_state()
        try:
            native, cartesian = next_native_action(model, state, plan, index, grips)
        except ValueError as error:
            feedback.update(status='kinematic_rejection', reason=str(error))
            log('interruptions.jsonl', {'command': command['id'], 'step': int(env.take_action_cnt[0]),
                                       'reason': 'control_kinematic_rejection', 'detail': str(error)})
            break
        before = int(env.take_action_cnt[0])
        env.take_action(native)
        if int(env.take_action_cnt[0]) != before + 1:
            raise RuntimeError('Native control action was not executed exactly once')
        grips.update({a: float(native[a + '_ee_joint_state'][0]) for a in ARMS})
        log('actions.jsonl', {'command': command['id'], 'step': before + 1, 'action': native,
                             'cartesian_target': cartesian, 'controls': feedback['controls']})
        measured = robot_state()
        if ((before + 1) % TICKS_PER_TASK_STEP == 0 or is_sample_tick(before + 1)
                or env.is_episode_end()):
            observe(f'step-{before + 1:04d}')
        interrupted = False
        for arm in ARMS:
            wrist_error = float(np.linalg.norm(np.asarray(measured[arm + '_ee_pose'][:3]) - cartesian[arm + '_ee_pose'][:3]))
            joint_error = float(np.max(np.abs(np.asarray(measured[arm + '_arm_joint_state']) - native[arm + '_arm_joint_state'])))
            opening_change = abs(float(measured[arm + '_gripper_measured_joints'][0]) - openings[arm])
            stopped = wrist_error > .035 or (watch_grip[arm] and opening_change > .003)
            feedback['tracking'].append({'arm': arm, 'step': before + 1, 'wrist_error_m': wrist_error,
                                         'joint_error_rad': joint_error, 'opening_change_m': opening_change, 'stopped': stopped})
            if stopped:
                log('interruptions.jsonl', {'command': command['id'], 'step': before + 1,
                                           'arm': arm, 'reason': 'tracking_or_gripper_change',
                                           'wrist_error_m': wrist_error, 'opening_change_m': opening_change})
                interrupted = True
        if interrupted:
            feedback['status'] = 'tracking_or_gripper_change'
            break
    if env.is_episode_end():
        feedback['status'] = 'episode_ended'
    return finish()
