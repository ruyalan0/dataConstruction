"""Single-environment joint execution using the native queue and physics backend.

The server and underlying evaluator count the same 4 ms ticks. The upstream
step limit is converted once to preserve the original episode duration.
"""
from copy import deepcopy

import numpy as np

from .control_timing import TICKS_PER_TASK_STEP, verify_environment_timing, rescale_task_budget, SOURCE_BUDGET_HZ


def public_robot_state(env):
    """Read the same public measurements as ObsManager without capturing RGB."""
    manager = env.robot_manager
    state = {}
    for robot in manager.robot_list:
        if robot.type != 'target' or robot.robot_type != 'arm':
            continue
        arm = robot.arm_name.split('_')[0]
        state[arm + '_arm_joint_state'] = manager.get_joint(robot, env_idx_list=[0])[0]
        state[arm + '_ee_pose'] = manager.get_real_endpose(robot, env_idx_list=[0])[0]
        state[arm + '_gripper_measured_joints'] = manager.get_end_effector_real_val(robot, env_idx_list=[0])[0]
        key = manager.process_name(robot.gripper_name)
        position = manager.control_manager.prev_control[0][key]['position']
        if robot.ee_type == 'gripper':
            low, high = robot.gripper_scale
            command = (position[0]-low if robot.gripper_move['sign'] == 1 else high-position[0]) / (high-low)
            state[key] = [command]
        else:
            state[key] = position
    return state


class TimedJointEnvironment:
    def __init__(self, env):
        verify_environment_timing(env)
        if env.num_envs != 1 or env.take_action_cnt != [0]:
            raise ValueError('Wrap one freshly reset evaluation environment')
        if type(env.step_lim) is not int or env.step_lim <= 0:
            raise ValueError('Invalid native task horizon')
        self.reward_updates = 0
        self.native = env
        self.take_action_cnt = [0]
        self.source_task_step_limit = env.step_lim
        self.source_task_horizon_seconds = env.step_lim / SOURCE_BUDGET_HZ
        env.step_lim = rescale_task_budget(env.step_lim)
        self.step_lim = env.step_lim

    def __getattr__(self, name):
        return getattr(self.native, name)

    def _control_frame(self, action):
        env = self.native
        env.validate_action_dict(action)
        if env.get_action_type(action) != 'joint':
            raise ValueError('Timed execution accepts only native joint actions')
        frame = {}
        for robot in env.robot_manager.robot_list:
            if robot.type != 'target':
                continue
            arm = env.robot_manager.process_name(robot.arm_name)
            frame[arm] = {'position': deepcopy(action[arm])}
            key = env.robot_manager.process_name(robot.gripper_name)
            if robot.ee_type == 'gripper':
                low, high = robot.gripper_scale
                opening = float(np.clip(action[key][0], 0, 1))
                if robot.gripper_move['sign'] != 1:
                    opening = 1 - opening
                primary = low + opening * (high - low)
                mimic = robot.gripper_move['mimic']
                frame[key] = {'position': [primary, primary * mimic[1] + mimic[2]]}
            elif robot.ee_type == 'hand':
                frame[key] = {'position': deepcopy(action[key])}
        if hasattr(env, 'support_arm_action') and env.support_arm_action[0]:
            frame.update(env.support_arm_action[0].pop(0))
        return frame

    def take_action(self, action):
        env = self.native
        if env.is_episode_end():
            raise RuntimeError('Cannot advance a terminated episode')
        if env.physx_monitor_enabled:
            env._check_physx_broken_envs()
        frame = self._control_frame(action)
        if hasattr(env, 'record_episode_actions'):
            env.record_episode_actions([action], [0])
        env.robot_manager.control_manager.push([0], [[frame]])
        env.step(env_idx_list=[0])
        self.take_action_cnt[0] += 1
        env.take_action_cnt[0] = self.take_action_cnt[0] // TICKS_PER_TASK_STEP
        if env.physx_monitor_enabled:
            env._check_physx_broken_envs()
            env._check_endpose_finite([0])
        if self.take_action_cnt[0] % TICKS_PER_TASK_STEP == 0:
            env.reward_manager.step(env_idx_list=[0])
            self.reward_updates += 1
            if hasattr(env, 'update_episode_diagnostics'):
                env.update_episode_diagnostics([0])
            env.is_episode_end()
            if getattr(env, 'interact', False) and not env.end_flag[0]:
                if hasattr(env, 'query_support_arm_traj'):
                    env.query_support_arm_traj(env_idx=0)
                if hasattr(env, 'check_support_arm_stable'):
                    env.check_support_arm_stable(env_idx=0)
