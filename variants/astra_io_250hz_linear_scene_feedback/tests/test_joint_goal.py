"""Exercise bounded references, native queues and truthful partial returns."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness')]
from physics_fixture import PhysicsFixture
from egg_harness.joint_goal import execute_control, controller_settings
from egg_harness.native_tools import ToolRegistry
from egg_harness.timed_joint import TimedJointEnvironment
from egg_harness.client import Robot, validate_goal_feedback
from egg_harness.control_timing import timing_record


class RateLimitedPhysics(PhysicsFixture):
    """A controllable rate limit with the real native validators/control queue."""
    def __init__(self, speed=1., task_steps=1000, contact_aperture=0., slip=False):
        super().__init__(task_steps)
        self.speed = speed
        self.contact_aperture = contact_aperture
        self.slip = slip

    def step(self, env_idx_list):
        previous = {arm: value.copy() for arm, value in self.positions.items()}
        fingers = deepcopy(self.openings)
        super().step(env_idx_list)
        for arm in self.positions:
            goal = self.positions[arm]
            self.positions[arm] = previous[arm] + np.clip(goal - previous[arm], -self.speed * .004, self.speed * .004)
            finger_goal = np.maximum(self.openings[arm], self.contact_aperture)
            self.openings[arm] = (np.asarray(fingers[arm]) + np.clip(finger_goal - fingers[arm], -.002, .002)).tolist()
        if self.slip:
            self.openings['left'] = [.024, .024]


def command(target=.4, gripper=1., **extra):
    return dict(id='goal-test', op='control_action', phase='transit', action=dict(
        arms=dict(left=dict(control='joint', joints=[target, 0., 0., 0., 0., 0.], gripper=gripper)), **extra))


def run_goal(native, request=None, grips=None):
    env = TimedJointEnvironment(native)
    captures = []

    def observe(tag):
        captures.append((tag, env.take_action_cnt[0]))
        return dict(step=env.take_action_cnt[0], state=native.state(), ended=env.is_episode_end())

    with tempfile.TemporaryDirectory() as folder, patch('egg_harness.joint_goal.check_configured_storage'):
        result = execute_control(env, Path(folder), request or command(), 'joint', native.model,
                                 native.state, observe, grips or dict(left=1., right=1.), lambda x: x.tolist())
        actions_path = Path(folder) / 'actions.jsonl'
        actions = [json.loads(line) for line in actions_path.read_text().splitlines()] if actions_path.exists() else []
    validate_goal_feedback(result, 0)
    return result, actions, captures


class GoalExecutionTests(unittest.TestCase):
    def test_reference_is_smooth_and_slow_actuator_returns_without_false_arrival(self):
        slow, slow_actions, _ = run_goal(RateLimitedPhysics(speed=.1), command(.12))
        fast, fast_actions, _ = run_goal(RateLimitedPhysics(speed=2.), command(.12))
        self.assertEqual(slow['action_feedback']['status'], 'observation_ready')
        self.assertFalse(slow['action_feedback']['target_reached'])
        self.assertEqual(fast['action_feedback']['status'], 'target_reached')
        self.assertGreater(slow['step'], fast['step'])
        self.assertLess(fast_actions[0]['action']['left_arm_joint_state'][0], .001)
        for left, right in zip(slow_actions, fast_actions):
            self.assertEqual(left['action'], right['action'])
        np.testing.assert_allclose(fast['state']['left_arm_joint_state'], [.12, 0., 0., 0., 0., 0.])

    def test_both_arms_share_progress_and_long_goal_returns_a_partial_segment(self):
        request = command(.1)
        request['action']['arms']['right'] = dict(control='joint', joints=[.6, 0., 0., 0., 0., 0.], gripper=1.)
        result, actions, _ = run_goal(RateLimitedPhysics(speed=2.), request)
        self.assertEqual(result['action_feedback']['status'], 'observation_ready')
        self.assertEqual(result['action_feedback']['reason'], 'trajectory_segment_complete')
        self.assertFalse(result['action_feedback']['target_reached'])
        for action in actions:
            np.testing.assert_allclose(action['action']['left_arm_joint_state'][0] * 6,
                                       action['action']['right_arm_joint_state'][0], atol=1e-12)
        self.assertGreater(result['state']['right_arm_joint_state'][0], 0.)
        self.assertLess(result['state']['right_arm_joint_state'][0], .6)
        self.assertLessEqual(result['action_feedback']['actual_duration_seconds'],
                             controller_settings()['max_motion_seconds'])

    def test_stationary_goal_advances_an_observation_window_and_preserves_task_clock(self):
        native = RateLimitedPhysics()
        result, actions, captures = run_goal(native, command(0.))
        self.assertEqual(result['step'], 60)
        self.assertEqual(result['action_feedback']['actual_duration_seconds'], .24)
        self.assertEqual(len(actions), 60)
        self.assertEqual(native.reward_ticks, list(range(10, 61, 10)))
        self.assertEqual(captures[-1], ('goal-test', 60))

    def test_blocked_arm_returns_stall_and_never_claims_arrival(self):
        result, _, _ = run_goal(RateLimitedPhysics(speed=0.))
        self.assertEqual(result['action_feedback']['status'], 'motion_stalled')
        self.assertEqual(result['action_feedback']['actual_duration_seconds'], .5)
        self.assertFalse(result['action_feedback']['target_reached'])

    def test_slow_progress_is_bounded_by_observation_interval(self):
        result, _, _ = run_goal(RateLimitedPhysics(speed=.01))
        self.assertEqual(result['action_feedback']['status'], 'observation_ready')
        self.assertEqual(result['action_feedback']['actual_duration_seconds'], .8)
        self.assertFalse(result['action_feedback']['target_reached'])

    def test_native_horizon_stops_before_arrival_and_keeps_actual_duration(self):
        native = RateLimitedPhysics(speed=.5, task_steps=10)
        result, actions, _ = run_goal(native)
        self.assertEqual(result['action_feedback']['status'], 'episode_ended')
        self.assertEqual(result['action_feedback']['actual_duration_seconds'], .4)
        self.assertEqual(native.take_action_cnt, [10])
        self.assertEqual(len(actions), 100)
        self.assertFalse(result['action_feedback']['target_reached'])

    def test_contact_limited_fingers_are_reported_without_grasp_claim(self):
        result, _, _ = run_goal(RateLimitedPhysics(contact_aperture=.02), command(0., gripper=0.))
        feedback = result['action_feedback']
        self.assertEqual(feedback['status'], 'gripper_settled_before_target')
        self.assertTrue(feedback['joint_targets_reached'])
        self.assertFalse(feedback['target_reached'])
        self.assertFalse(feedback['retention_verified'])
        np.testing.assert_array_equal(result['state']['left_arm_joint_state'], np.zeros(6))
        self.assertGreaterEqual(feedback['actual_duration_seconds'], controller_settings()['gripper_close_min_seconds'])

    def test_existing_held_gripper_change_guard_returns_early(self):
        native = RateLimitedPhysics(contact_aperture=.02, slip=True)
        native.openings['left'] = [.02, .02]
        native.robot_manager.control_manager.prev_control[0]['left_ee_joint_state']['position'] = [.02, .02]
        result, _, _ = run_goal(native, command(.4, gripper=0.), dict(left=0., right=1.))
        self.assertEqual(result['step'], 1)
        self.assertEqual(result['action_feedback']['status'], 'tracking_or_gripper_change')

    def test_legacy_duration_or_out_of_limit_goal_is_rejected_before_motion(self):
        for request in [command(steps=100), command(settle=2)]:
            result, actions, _ = run_goal(RateLimitedPhysics(), request)
            self.assertEqual(result['action_feedback']['status'], 'rejected_before_motion')
            self.assertEqual(actions, [])
        request = command()
        request['action']['arms']['left']['joints'][5] = 4.
        result, actions, _ = run_goal(RateLimitedPhysics(), request)
        self.assertEqual(result['action_feedback']['status'], 'rejected_before_motion')
        self.assertEqual(actions, [])

    def test_feedback_rejects_fabricated_duration_and_false_arrival(self):
        result, _, _ = run_goal(RateLimitedPhysics(), command(.1))
        corrupt = deepcopy(result)
        corrupt['action_feedback']['actual_duration_seconds'] += .004
        with self.assertRaisesRegex(ValueError, 'duration'):
            validate_goal_feedback(corrupt, 0)
        corrupt = deepcopy(result)
        corrupt['action_feedback']['joint_targets_reached'] = False
        with self.assertRaisesRegex(ValueError, 'Unsettled'):
            validate_goal_feedback(corrupt, 0)

    def test_partial_goal_continues_only_after_explicit_model_reissue(self):
        native = RateLimitedPhysics(speed=2.)
        env = TimedJointEnvironment(native)
        def observe(tag):
            return dict(step=env.take_action_cnt[0], state=native.state(), ended=env.is_episode_end())
        with tempfile.TemporaryDirectory() as folder, patch('egg_harness.joint_goal.check_configured_storage'):
            grips = dict(left=1., right=1.)
            statuses = []
            for index in range(3):
                previous = env.take_action_cnt[0]
                request = command(.6)
                request['id'] = str(index)
                result = execute_control(env, Path(folder), request, 'joint', native.model,
                                         native.state, observe, grips, lambda value: value.tolist())
                validate_goal_feedback(result, previous)
                statuses.append(result['action_feedback']['status'])
                self.assertEqual(env.goal_submissions, index + 1)
            self.assertEqual(statuses, ['observation_ready', 'observation_ready', 'target_reached'])
            goals = [json.loads(line) for line in (Path(folder) / 'goals.jsonl').read_text().splitlines()]
            self.assertEqual(len(goals), 3)
            self.assertTrue(all(row['action']['left_arm_joint_state'][0] == .6 for row in goals))
            np.testing.assert_allclose(native.positions['left'], [.6, 0., 0., 0., 0., 0.])

    def test_partial_goal_cannot_fabricate_arrival_or_verified_retention(self):
        result, _, _ = run_goal(RateLimitedPhysics())
        corrupt = deepcopy(result)
        corrupt['action_feedback'].update(status='target_reached', target_reached=True,
                                          joint_targets_reached=True, gripper_settled=True)
        with self.assertRaisesRegex(ValueError, 'Partial trajectory'):
            validate_goal_feedback(corrupt, 0)
        corrupt = deepcopy(result)
        corrupt['action_feedback']['retention_verified'] = True
        with self.assertRaisesRegex(ValueError, 'retention'):
            validate_goal_feedback(corrupt, 0)

    def test_real_file_client_accepts_controller_feedback_and_records_duration(self):
        native = RateLimitedPhysics()
        env = TimedJointEnvironment(native)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            for name in ('commands', 'responses', 'trace'):
                (output / name).mkdir()
            robot = Robot.__new__(Robot)
            robot.episode, robot.trace = output, output / 'trace'
            robot.counter, robot.session = 0, 'goal-test'
            robot.obs = dict(step=0)

            def observe(tag):
                return dict(step=env.take_action_cnt[0], state=native.state(), ended=env.is_episode_end(),
                            cameras={}, instruction='test', budget={}, action_interface='joint',
                            control_timing=timing_record())

            def serve(_):
                path = next((output / 'commands').glob('*.json'))
                request = json.loads(path.read_text())
                result = execute_control(env, output, request, 'joint', native.model, native.state,
                                         observe, dict(left=1., right=1.), lambda x: x.tolist())
                (output / 'responses' / path.name).write_text(json.dumps(result, default=lambda x: x.tolist()))

            with patch('egg_harness.client.time.sleep', side_effect=serve), \
                 patch('egg_harness.joint_goal.check_configured_storage'):
                result = robot.command('control_action', action=command(.1)['action'], phase='transit')
            self.assertEqual(result['action_feedback']['status'], 'target_reached')
            logged = json.loads((robot.trace / 'commands.jsonl').read_text())
            self.assertNotIn('steps', logged['command']['action'])
            self.assertEqual(logged['step'], result['step'])


class GoalContractTests(unittest.TestCase):
    def test_schema_and_local_validation_require_goals_without_durations(self):
        registry = ToolRegistry({'action_interface': 'joint'})
        tool = next(tool for tool in registry.definitions if tool['name'] == 'control_arms')
        properties = tool['parameters']['properties']
        self.assertEqual(set(properties), {'assessment', 'plan', 'memory', 'left', 'right'})
        args = dict(assessment='observed', plan='goal', memory='memory', left=None,
                    right=dict(control='joint', joints=[.1, 0., 0., 0., 0., 0.], gripper=None))
        decision = registry.decision('control_arms', json.dumps(args))
        self.assertEqual(set(decision['action']['control_arms']), {'arms'})
        for field in ['steps', 'step', 'settle', 'duration', 'speed', 'phase']:
            with self.assertRaises(ValueError):
                registry.decision('control_arms', json.dumps(dict(args, **{field: 1})))


if __name__ == '__main__':
    unittest.main()
