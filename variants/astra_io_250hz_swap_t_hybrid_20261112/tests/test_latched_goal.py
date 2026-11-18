"""Goal submissions must preserve native per-tick physics and gripper filtering."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness')]
from physics_fixture import PhysicsFixture, native_action
from test_joint_goal import RateLimitedPhysics, command, run_goal
from egg_harness.clocks import PHYSICS_HZ, TASK_HZ, clock_metadata
from egg_harness.control_timing import configure_timing, timing_record, validate_timing_record
from egg_harness.joint_goal import execute_control, controller_settings
from egg_harness.timed_joint import TimedJointEnvironment
from egg_harness.client import validate_goal_feedback


class LatchedGoalTests(unittest.TestCase):
    def test_submission_copies_goal_and_does_not_advance_time_or_consume_support(self):
        native = PhysicsFixture()
        native.support_arm_action = [[{'support_arm': {'position': [1.]}}]]
        env = TimedJointEnvironment(native)
        action = native_action(.4, gripper=0.)
        with patch.object(native, 'is_episode_end', wraps=native.is_episode_end) as check_end:
            env.set_joint_goal(action)
            check_end.assert_not_called()
        self.assertEqual(env.take_action_cnt, [0])
        self.assertEqual(native.take_action_cnt, [0])
        self.assertEqual(native.frames, [])
        self.assertEqual(native.queued_frames, [])
        self.assertEqual(len(native.support_arm_action[0]), 1)
        action['left_arm_joint_state'][0] = 1.2
        action['left_ee_joint_state'][0] = 1.
        env.advance()
        self.assertEqual(native.queued_frames[0]['left_arm_joint_state']['position'][0], .4)
        self.assertEqual(native.queued_frames[0]['left_ee_joint_state']['position'], [0., 0.])
        self.assertEqual(env.goal_submissions, 1)

    def test_one_submission_per_controller_action_and_one_validation_per_submission(self):
        native = RateLimitedPhysics(speed=.5)
        with patch.object(native, 'validate_action_dict', wraps=native.validate_action_dict) as validate, \
             patch.object(TimedJointEnvironment, '_control_frame', autospec=True,
                          side_effect=TimedJointEnvironment._control_frame) as compile_frame:
            result, _, _ = run_goal(native)
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(compile_frame.call_count, 1)
        self.assertEqual(result['action_feedback']['goal_submissions'], 1)
        self.assertLessEqual(result['step'] / PHYSICS_HZ, controller_settings()['max_motion_seconds'])
        self.assertEqual(len(native.queued_frames), result['step'])
        self.assertLess(native.queued_frames[0]['left_arm_joint_state']['position'][0], .001)

    def test_latched_gripper_keeps_raw_goal_and_matches_repeated_native_commands(self):
        latched, repeated = RateLimitedPhysics(), RateLimitedPhysics()
        env, reference = TimedJointEnvironment(latched), TimedJointEnvironment(repeated)
        goal = native_action(.3, gripper=0.)
        env.set_joint_goal(goal)
        for _ in range(50):
            env.advance()
            reference.take_action(goal)
            for arm in ('left', 'right'):
                np.testing.assert_array_equal(latched.positions[arm], repeated.positions[arm])
                np.testing.assert_array_equal(latched.openings[arm], repeated.openings[arm])
        self.assertEqual(latched.frames, repeated.frames)
        self.assertEqual(env.goal_submissions, 1)
        self.assertEqual(reference.goal_submissions, 50)
        self.assertGreater(latched.frames[0]['left_ee_joint_state']['position'][0], 0.)
        self.assertEqual(latched.openings['left'], [0., 0.])
        self.assertTrue(all(row['left_ee_joint_state']['position'] == [0., 0.]
                            for row in latched.queued_frames))

    def test_support_trajectory_is_consumed_per_tick_without_sticking_to_cached_goal(self):
        native = PhysicsFixture()
        trajectory = [{'support_arm': {'position': [number]}} for number in (1., 2., 3.)]
        native.support_arm_action = [deepcopy(trajectory)]
        env = TimedJointEnvironment(native)
        env.set_joint_goal(native_action(.2))
        for _ in range(4):
            env.advance()
        self.assertEqual([row['support_arm'] for row in native.queued_frames[:3]],
                         [row['support_arm'] for row in trajectory])
        self.assertNotIn('support_arm', native.queued_frames[3])
        self.assertEqual(native.support_arm_action, [[]])
        self.assertEqual(env.goal_submissions, 1)

    def test_new_goal_replaces_old_goal_without_motion_and_failed_goal_preserves_it(self):
        native = PhysicsFixture()
        env = TimedJointEnvironment(native)
        env.set_joint_goal(native_action(.2))
        env.advance()
        self.assertEqual(native.positions['left'][0], .2)
        env.set_joint_goal(native_action(.4))
        self.assertEqual(native.positions['left'][0], .2)
        self.assertEqual(env.take_action_cnt, [1])
        invalid = native_action(.9)
        invalid['left_arm_joint_state'] = [1.]
        with self.assertRaises(ValueError):
            env.set_joint_goal(invalid)
        env.advance()
        self.assertEqual(native.positions['left'][0], .4)
        self.assertEqual(env.goal_submissions, 2)

    def test_missing_goal_pending_native_controls_and_ended_episode_cannot_advance(self):
        native = PhysicsFixture(task_steps=1)
        env = TimedJointEnvironment(native)
        with self.assertRaisesRegex(RuntimeError, 'Submit a joint goal'):
            env.advance()
        env.set_joint_goal(native_action())
        native.robot_manager.control_manager.push([0], [[{}]])
        with self.assertRaisesRegex(RuntimeError, 'queued'):
            env.set_joint_goal(native_action(.1))
        with self.assertRaisesRegex(RuntimeError, 'empty native queue'):
            env.advance()
        native.robot_manager.control_manager.pop([0])
        for _ in range(PHYSICS_HZ // TASK_HZ):
            env.advance()
        with self.assertRaisesRegex(RuntimeError, 'terminated'):
            env.advance()
        with self.assertRaisesRegex(RuntimeError, 'terminated'):
            env.set_joint_goal(native_action(.1))
        self.assertEqual(env.take_action_cnt, [10])

    def test_multiple_model_actions_have_one_goal_record_each_and_complete_tick_evidence(self):
        native = RateLimitedPhysics(speed=2.)
        env = TimedJointEnvironment(native)
        def observe(tag):
            return dict(step=env.take_action_cnt[0], state=native.state(), ended=env.is_episode_end())
        with tempfile.TemporaryDirectory() as folder, patch('egg_harness.joint_goal.check_configured_storage'):
            output = Path(folder)
            grips = dict(left=1., right=1.)
            for index, target in enumerate((.2, -.2)):
                request = command(target)
                request['id'] = f'goal-{index}'
                previous_step = env.take_action_cnt[0]
                result = execute_control(env, output, request, 'joint', native.model, native.state,
                                         observe, grips, lambda value: value.tolist())
                validate_goal_feedback(result, previous_step)
                self.assertEqual(result['action_feedback']['goal_submissions'], 1)
            goals = [json.loads(row) for row in (output / 'goals.jsonl').read_text().splitlines()]
            actions = [json.loads(row) for row in (output / 'actions.jsonl').read_text().splitlines()]
        self.assertEqual([row['command'] for row in goals], ['goal-0', 'goal-1'])
        self.assertEqual(goals[0]['step'], 0)
        self.assertGreater(goals[1]['step'], 0)
        self.assertEqual(len(actions), env.take_action_cnt[0])
        self.assertEqual([row['step'] for row in actions], list(range(1, env.take_action_cnt[0] + 1)))
        self.assertEqual(env.goal_submissions, 2)

    def test_rejected_goal_has_no_submission_and_feedback_cannot_fabricate_one(self):
        result, _, _ = run_goal(RateLimitedPhysics(), command(steps=1))
        self.assertEqual(result['action_feedback']['goal_submissions'], 0)
        result['action_feedback']['goal_submissions'] = 1
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            validate_goal_feedback(result, 0)
        result, _, _ = run_goal(RateLimitedPhysics())
        result['action_feedback']['goal_submissions'] = 2
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            validate_goal_feedback(result, 0)


class SimulationClockTests(unittest.TestCase):
    def test_timing_distinguishes_physics_from_goals_and_rejects_old_protocol(self):
        timing = timing_record()
        self.assertEqual(timing['physics_hz'], 250)
        self.assertEqual(timing['controller_check_hz'], 250)
        self.assertEqual(timing['task_hz'], 25)
        self.assertEqual(timing['observation_hz'], 25)
        self.assertEqual(timing['goal_submission'], 'once_per_model_action')
        for obsolete in ('action_hz', 'action_interval_seconds', 'physics_steps_per_action'):
            self.assertNotIn(obsolete, timing)
        for field in ('physics_hz', 'controller_check_hz', 'task_hz', 'observation_hz'):
            corrupt = dict(timing, **{field: 1})
            with self.assertRaises(ValueError):
                validate_timing_record(corrupt)
        with self.assertRaises(ValueError):
            validate_timing_record(dict(timing, protocol='native_joint_goal_v1'))
        self.assertEqual(clock_metadata(), {key: timing[key] for key in clock_metadata()})

    def test_configuration_preserves_native_physics_camera_and_task_rates(self):
        config = SimpleNamespace(sim=SimpleNamespace(dt=.004, decimation=1, render_interval=1),
                                 eval_cfg=SimpleNamespace(observation=SimpleNamespace(collect_freq=25)),
                                 camera=SimpleNamespace(default_frequency=250))
        self.assertEqual(configure_timing(config), timing_record())
        self.assertEqual(config.sim.dt, .004)
        self.assertEqual(config.sim.decimation, 1)
        self.assertEqual(config.sim.render_interval, 10)
        self.assertEqual(config.camera.default_frequency, 25)
        self.assertEqual(config.eval_cfg.observation.collect_freq, 25)
        config.sim.decimation = 10
        with self.assertRaisesRegex(ValueError, 'decimation'):
            configure_timing(config)


if __name__ == '__main__':
    unittest.main()
