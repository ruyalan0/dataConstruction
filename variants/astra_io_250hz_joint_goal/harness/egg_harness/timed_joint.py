"""Latch goals once, then advance reference trajectories and native physics.

Native EvalEnv.step requires one queued frame per physics tick. Reusing the
unfiltered gripper goal preserves MetaControl's measured-state filtering.
"""
from copy import deepcopy

import numpy as np

from .clocks import PHYSICS_TICKS_PER_TASK_STEP
from .control_timing import verify_environment_timing


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
        self.native = env
        self.take_action_cnt = [0]
        self.step_lim = env.step_lim * PHYSICS_TICKS_PER_TASK_STEP
        self.goal_submissions = 0
        self._goal_action = None
        self._goal_frame = None
        self._trajectory = None
        self._reference_tick = 0

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
        return frame

    def set_joint_goal(self, action, trajectory=None):
        """Validate and convert an absolute goal without advancing simulation."""
        env = self.native
        # is_episode_end() consults reward state; submission itself must not
        # add a task check. advance() performs the native pre-tick check.
        if env.end_flag[0] or env.take_action_cnt[0] >= env.step_lim:
            raise RuntimeError('Cannot submit a goal to a terminated episode')
        if 0 not in env.robot_manager.control_manager.get_empty([0]):
            raise RuntimeError('Cannot replace a goal while native controls are queued')
        goal_action = deepcopy(action)
        goal_frame = self._control_frame(goal_action)
        self._goal_action, self._goal_frame = goal_action, goal_frame
        self._trajectory = trajectory
        self._reference_tick = 0
        self.goal_submissions += 1

    def advance(self):
        """Advance one physics tick toward the latched goal, not a new action."""
        env = self.native
        if self._goal_frame is None:
            raise RuntimeError('Submit a joint goal before advancing physics')
        if env.is_episode_end():
            raise RuntimeError('Cannot advance a terminated episode')
        if 0 not in env.robot_manager.control_manager.get_empty([0]):
            raise RuntimeError('Expected an empty native queue before a physics tick')
        if env.physx_monitor_enabled:
            env._check_physx_broken_envs()
        frame = self._goal_frame
        reference = deepcopy(self._goal_action)
        if self._trajectory is not None:
            frame = dict(frame)
            positions = self._trajectory.positions(self._reference_tick + 1)
            for arm, joints in positions.items():
                key = arm + '_arm_joint_state'
                frame[key] = {'position': joints.tolist()}
                reference[key] = joints.copy()
        # Support trajectories evolve every tick; never cache them with targets.
        if hasattr(env, 'support_arm_action') and env.support_arm_action[0]:
            frame = dict(frame)
            frame.update(env.support_arm_action[0].pop(0))
        if hasattr(env, 'record_episode_actions'):
            env.record_episode_actions([reference], [0])
        env.robot_manager.control_manager.push([0], [[frame]])
        env.step(env_idx_list=[0])
        self.take_action_cnt[0] += 1
        self._reference_tick += 1
        env.take_action_cnt[0] = self.take_action_cnt[0] // PHYSICS_TICKS_PER_TASK_STEP
        if env.physx_monitor_enabled:
            env._check_physx_broken_envs()
            env._check_endpose_finite([0])
        if self.take_action_cnt[0] % PHYSICS_TICKS_PER_TASK_STEP == 0:
            env.reward_manager.step(env_idx_list=[0])
            if hasattr(env, 'update_episode_diagnostics'):
                env.update_episode_diagnostics([0])
            env.is_episode_end()
            if getattr(env, 'interact', False) and not env.end_flag[0]:
                if hasattr(env, 'query_support_arm_traj'):
                    env.query_support_arm_traj(env_idx=0)
                if hasattr(env, 'check_support_arm_stable'):
                    env.check_support_arm_stable(env_idx=0)

        return reference

    def take_action(self, action):
        """Compatibility for development helpers that intentionally change each tick."""
        self.set_joint_goal(action)
        self.advance()
