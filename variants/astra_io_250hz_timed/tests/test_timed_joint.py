"""Exercise real native validation/queues and timed execution with simulated physics."""
import ast
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE / 'harness'), str(ROOT / 'scripts')]
from egg_harness.action_control import execute_control, matrix_pose
from egg_harness.kinematics import Kinematics
from egg_harness.native_tools import ToolRegistry
from egg_harness.timed_joint import TimedJointEnvironment, public_robot_state
from egg_harness.control_timing import timing_record


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


def run_segment(native, steps, settle=0):
    env = TimedJointEnvironment(native)
    observations = []
    def observe(tag):
        observations.append(env.take_action_cnt[0])
        return dict(step=env.take_action_cnt[0], state=native.state(), ended=env.is_episode_end())
    command = dict(id='segment', op='control_action', phase='transit', action=dict(
        arms={side: dict(control='joint', joints=[1., 0., 0., 0., 0., 0.], gripper=1.)
              for side in ('left', 'right')}, steps=steps, settle=settle))
    with tempfile.TemporaryDirectory() as directory, \
         patch('egg_harness.action_control.check_configured_storage'):
        feedback = execute_control(env, Path(directory), command, 'joint', native.model,
                                   native.state, observe, dict(left=1., right=1.), lambda x: x.tolist())
        actions = [json.loads(line) for line in (Path(directory) / 'actions.jsonl').read_text().splitlines()]
    return env, feedback['action_feedback'], observations, actions


class TimedExecutionTests(unittest.TestCase):
    def test_public_measurements_keep_command_echo_separate_without_camera_capture(self):
        native = PhysicsFixture()
        native.openings['left'] = [.02, .02]
        state = native.state()
        self.assertEqual(state['left_ee_joint_state'], [1.])
        self.assertEqual(state['left_gripper_measured_joints'], [.02, .02])
        np.testing.assert_allclose(state['left_ee_pose'], matrix_pose(native.model.forward(np.zeros(6))))

    def test_native_default_still_uses_ten_ticks_but_timed_control_uses_one(self):
        original = PhysicsFixture()
        with redirect_stdout(io.StringIO()):
            original.take_action_batch([native_action(.1)], [0])
        self.assertEqual(len(original.frames), 10)
        self.assertEqual(original.take_action_cnt, [1])
        self.assertEqual(original.reward_ticks, [10])
        native = PhysicsFixture()
        env = TimedJointEnvironment(native)
        env.take_action(native_action(.1))
        self.assertEqual(len(native.frames), 1)
        self.assertEqual(env.take_action_cnt, [1])
        self.assertEqual(native.take_action_cnt, [0])
        self.assertEqual(native.reward_ticks, [])
        for _ in range(9):
            env.take_action(native_action(.1))
        self.assertEqual(native.take_action_cnt, [1])
        self.assertEqual(native.reward_ticks, [10])

    def test_200_task_steps_last_eight_seconds_and_rewards_keep_their_cadence(self):
        native = PhysicsFixture()
        env = TimedJointEnvironment(native)
        for _ in range(1999):
            env.take_action(native_action())
        self.assertFalse(env.is_episode_end())
        env.take_action(native_action())
        self.assertEqual(len(native.frames), 2000)
        self.assertEqual(len(native.frames) * .004, 8.)
        self.assertEqual(native.take_action_cnt, [200])
        self.assertEqual(native.reward_ticks, list(range(10, 2001, 10)))
        self.assertEqual(native.diagnostic_ticks, native.reward_ticks)
        self.assertTrue(env.is_episode_end())
        with self.assertRaises(RuntimeError):
            env.take_action(native_action())

    def test_segment_interpolation_gripper_first_tick_and_explicit_settle(self):
        native = PhysicsFixture()
        env, feedback, captures, actions = run_segment(native, 125, settle=2)
        self.assertEqual(feedback['executed_steps'], 127)
        self.assertEqual(feedback['status'], 'completed')
        self.assertEqual(len(feedback['tracking']), 254)
        self.assertEqual(len(actions), 127)
        self.assertEqual(native.take_action_cnt, [12])
        self.assertEqual(captures, [*range(10, 121, 10), 127])
        self.assertAlmostEqual(native.frames[0]['left_arm_joint_state']['position'][0], 1/125)
        self.assertEqual(native.frames[0]['left_ee_joint_state']['position'], [.044, .044])
        for frame in native.frames[124:]:
            self.assertEqual(frame['left_arm_joint_state']['position'][0], 1.)

    def test_horizon_cuts_a_prefix_without_speeding_up_the_trajectory(self):
        native = PhysicsFixture(task_steps=10)
        env, feedback, _, _ = run_segment(native, 125)
        self.assertEqual(feedback['status'], 'episode_ended')
        self.assertEqual(feedback['executed_steps'], 100)
        self.assertAlmostEqual(native.frames[-1]['left_arm_joint_state']['position'][0], .8)
        self.assertEqual(native.take_action_cnt, [10])

    def test_gripper_sign_mimic_and_support_script_use_native_conventions(self):
        native = PhysicsFixture()
        robot = native.robot_manager.robot_list[0]
        robot.gripper_move = dict(sign=-1, mimic=[0, -1, .01])
        native.support_arm_action = [[dict(support=dict(position=[3.]))]]
        env = TimedJointEnvironment(native)
        env.take_action(native_action(gripper=.25))
        frame = native.queued_frames[0]
        np.testing.assert_allclose(frame['left_ee_joint_state']['position'], [.033, -.023])
        np.testing.assert_allclose(native.frames[0]['left_ee_joint_state']['position'], [.0352, -.0252])
        self.assertEqual(frame['support']['position'], [3.])
        env.take_action(native_action())
        self.assertNotIn('support', native.queued_frames[1])

    def test_existing_tracking_guard_still_returns_actual_prefix(self):
        native = PhysicsFixture()
        original_step = native.step
        def deviated_step(env_idx_list):
            original_step(env_idx_list)
            native.positions['left'][0] += 1.
        native.step = deviated_step
        _, feedback, _, actions = run_segment(native, 125)
        self.assertEqual(feedback['status'], 'tracking_or_gripper_change')
        self.assertEqual(feedback['executed_steps'], 1)
        self.assertEqual(len(actions), 1)


class ReferenceProtocolTests(unittest.TestCase):
    def test_all_remote_decisions_validate_and_durations_match_the_44_second_horizon(self):
        records = [json.loads(line) for line in (HERE / 'tests/fixtures/qwen_actions.jsonl').read_text().splitlines()]
        registry = ToolRegistry({'action_interface': 'joint'})
        elapsed = 0
        decisions = 0
        for planned, executed in zip(records[::2], records[1::2]):
            self.assertEqual(planned['status'], 'planned')
            self.assertEqual(executed['status'], 'executed')
            self.assertEqual(planned['decision'], executed['decision'])
            action = planned['action']
            registry.decision('control_arms', json.dumps(dict(
                assessment='remote timing fixture', plan='next measured goal', memory='fixture',
                left=action['arms']['left'], right=action['arms']['right'],
                steps=action['steps'], settle=0)))
            self.assertEqual(planned['simulation_steps'], action['steps'])
            self.assertAlmostEqual(planned['requested_duration_s'], action['steps']/250)
            actual_ticks = min(action['steps'], 11000-elapsed)
            self.assertAlmostEqual(executed['actual_duration_s'], actual_ticks/250)
            elapsed += actual_ticks
            decisions += 1
        self.assertEqual(decisions, 133)
        self.assertEqual(elapsed, 11000)
        self.assertGreater(sum(row['action']['steps'] for row in records[::2]), elapsed)

    def test_duration_bound_is_shared_by_model_schema_and_executor(self):
        registry = ToolRegistry({'action_interface': 'joint'})
        args = dict(assessment='observed', plan='hold', memory='memory', left=None, right=None, settle=0)
        for steps in (1, 117, 125, 2500):
            decision = registry.decision('control_arms', json.dumps(dict(args, steps=steps)))
            self.assertEqual(decision['action']['control_arms']['steps'], steps)
        for steps in (0, 2501, True, 125.5):
            with self.assertRaises(ValueError):
                registry.decision('control_arms', json.dumps(dict(args, steps=steps)))


class ServerBudgetTests(unittest.TestCase):
    def test_server_reports_task_budget_and_control_ticks_at_a_non_boundary(self):
        sys.path.insert(0, str(HERE / 'harness/scripts'))
        import server_loop
        native = PhysicsFixture()
        native.success = [False]
        native.get_obs = lambda: dict(state=native.state())
        env = TimedJointEnvironment(native)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'control-timing.json').write_text(json.dumps(timing_record()))
            commands_written = False
            def capture(environment):
                nonlocal commands_written
                if not commands_written:
                    commands_written = True
                    (folder / 'commands/0001.json').write_text(json.dumps(dict(
                        id='0001', op='control_action', phase='transit',
                        action=dict(arms={}, steps=125, settle=0))))
                    (folder / 'commands/zz-runner-finish.json').write_text(json.dumps(
                        dict(id='zz-runner-finish', op='finish')))
                return dict(state=native.state(), instruction='Hold the robot',
                            vision={name: dict(color=np.zeros((3, 3, 3), dtype=np.uint8))
                                    for name in ('cam_head', 'cam_left_wrist', 'cam_right_wrist')})
            with patch.object(server_loop, 'capture_public_observation', side_effect=capture), \
                 patch.object(server_loop, 'validate_camera_rig', return_value='a' * 64), \
                 patch('egg_harness.action_control.check_configured_storage'):
                server_loop.run_server(env, folder, [0], 'frozen', 'general_pickup')
            response = json.loads((folder / 'responses/0001.json').read_text())
            self.assertEqual(response['step'], 125)
            self.assertEqual(response['action_feedback']['executed_steps'], 125)
            self.assertEqual(response['budget']['total_native_steps'], 200)
            self.assertEqual(response['budget']['executed_native_steps'], 12)
            self.assertEqual(response['budget']['remaining_native_steps'], 188)
            self.assertEqual(response['budget']['total_control_ticks'], 2000)
            self.assertEqual(response['budget']['remaining_control_ticks'], 1875)
            self.assertEqual(response['budget']['elapsed_simulation_seconds'], .5)
            self.assertEqual(response['budget']['task_horizon_seconds'], 8.)
            result = json.loads((folder / 'runner-result.json').read_text())
            self.assertEqual(result['task_steps'], 12)
            self.assertEqual(result['steps'], 125)
            self.assertFalse(result['native_terminal'])


if __name__ == '__main__':
    unittest.main()
