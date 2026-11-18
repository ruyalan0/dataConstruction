"""Native validators/queue fixture, adapted from the parent timing tests."""
from copy import deepcopy
import ast
import importlib.util
import json
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
import numpy as np
HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE / 'harness'), str(ROOT / 'scripts')]
from egg_harness.action_control import matrix_pose
from egg_harness.kinematics import Kinematics
from egg_harness.timed_joint import public_robot_state


def native_methods():
    path = ROOT / 'dependencies/RoboDojo/src/eval_client/eval_env.py'
    names = {'take_action_batch', 'process_control_info', 'validate_action_dict', 'get_action_type'}
    functions = [node for node in ast.walk(ast.parse(path.read_text()))
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = dict(np=np, deepcopy=deepcopy)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
    return {name: namespace[name] for name in names}


def native_action(value=0., gripper=1.):
    result = {}
    for side in ('left', 'right'):
        result[side + '_arm_joint_state'] = [value, 0., 0., 0., 0., 0.]
        result[side + '_ee_joint_state'] = [gripper]
    return result


class PhysicsFixture:
    """Native validators, old interpolator and real queue; no Isaac or contacts."""
    def __init__(self, task_steps=200):
        self.dt, self.num_envs, self.step_lim = .004, 1, task_steps
        self.take_action_cnt, self.end_flag = [0], [False]
        self.physx_monitor_enabled = False
        self.robot_action_dim_info = dict(arm_dim=[6, 6], ee_dim=[1, 1])
        self.obs_manager = SimpleNamespace(collect_freq=25, collect_interval=10.)
        self.sim = SimpleNamespace(unwrapped=SimpleNamespace(step_dt=.004))
        self.frames, self.queued_frames, self.reward_ticks, self.diagnostic_ticks = [], [], [], []
        self.model = Kinematics(json.loads((HERE / 'harness/configs/x5-kinematics.json').read_text()))
        self.positions = {side: np.zeros(6) for side in ('left', 'right')}
        self.openings = {side: [.044, .044] for side in ('left', 'right')}
        robots = [SimpleNamespace(type='target', robot_type='arm', arm_name=side + '_arm', gripper_name=side + '_ee',
                                 ee_type='gripper', gripper_scale=[0., .044],
                                 gripper_move=dict(sign=1, mimic=[0, 1, 0]))
                  for side in ('left', 'right')]
        self.robot_manager = SimpleNamespace(robot_list=robots,
            process_name=lambda name: name + '_joint_state',
            restore_name=lambda name: name.removesuffix('_joint_state'),
            get_robot_by_gripper_name=lambda name: next(robot for robot in robots if robot.gripper_name == name),
            get_robot_obs_name=lambda: list(native_action()),
            get_joint=lambda robot, env_idx_list: {0: self.positions[robot.arm_name.split('_')[0]]},
            get_real_endpose=lambda robot, env_idx_list:
                {0: matrix_pose(self.model.forward(self.positions[robot.arm_name.split('_')[0]]))},
            get_end_effector_real_val=lambda robot, env_idx_list:
                {0: self.openings[robot.arm_name.split('_')[0]]})
        path = ROOT / 'dependencies/RoboDojo/env/robot_manager/control_manager.py'
        spec = importlib.util.spec_from_file_location('timing_test_native_queue', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.robot_manager.control_manager = module.ControlManager(1, self.robot_manager)
        self.robot_manager.control_manager.prev_control[0] = {
            side + suffix: dict(position=(self.positions[side].tolist() if suffix == '_arm_joint_state' else [.044, .044]))
            for side in self.positions for suffix in ('_arm_joint_state', '_ee_joint_state')}
        original_push = self.robot_manager.control_manager.push
        def push(indices, sequences):
            self.queued_frames.extend(deepcopy(sequences[0]))
            original_push(indices, sequences)
        self.robot_manager.control_manager.push = push
        self.reward_manager = SimpleNamespace(step=lambda env_idx_list: self.reward_ticks.append(len(self.frames)))
        for name, method in native_methods().items():
            setattr(self, name, MethodType(method, self))

    def step(self, env_idx_list):
        frame = self.robot_manager.control_manager.pop(env_idx_list)[0].control_info_dict
        self.frames.append(deepcopy(frame))
        for side in self.positions:
            self.positions[side] = np.asarray(frame[side + '_arm_joint_state']['position'])
            self.openings[side] = frame[side + '_ee_joint_state']['position']

    def have_empty(self, env_idx_list):
        return bool(self.robot_manager.control_manager.get_empty(env_idx_list))

    def is_episode_end(self):
        if self.take_action_cnt[0] >= self.step_lim:
            self.end_flag[0] = True
        return self.end_flag[0]

    def update_episode_diagnostics(self, env_idx_list):
        self.diagnostic_ticks.append(len(self.frames))

    def state(self):
        return public_robot_state(self)
