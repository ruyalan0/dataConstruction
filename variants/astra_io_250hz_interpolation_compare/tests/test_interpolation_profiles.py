"""Validate actual native frames, support timing, replay and profile contracts."""
from copy import deepcopy
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE / 'harness'), str(HERE / 'tests')]
from physics_fixture import PhysicsFixture, native_action
from test_joint_goal import RateLimitedPhysics, command, run_goal
from egg_harness.interpolation_profiles import make_trajectory, selected_profile, PROFILE_NAMES
from egg_harness.joint_goal import compile_goal, controller_settings
from egg_harness.timed_joint import TimedJointEnvironment
from egg_harness.prompting import build_system


class InterpolationComparisonTests(unittest.TestCase):
    def trajectory(self, native, target=.8, grip=0.):
        plan = compile_goal(native.model, native.state(), command(target, grip)['action'],
                            dict(left=1., right=1.))
        return make_trajectory(plan, controller_settings())

    def test_native_joint_and_gripper_frames_match_the_original_function_exactly(self):
        with patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE='native'):
            native = PhysicsFixture()
            env = TimedJointEnvironment(native)
            action = native_action(.8, 0.)
            frame = env._control_frame(action)
            expected = native.process_control_info(deepcopy(frame), env_idx=0)
            trajectory = self.trajectory(native)
            # Match both arms to the identical native action used above.
            trajectory.targets['right'] = np.array([.8, 0., 0., 0., 0., 0.])
            env.set_joint_goal(action, trajectory)
            self.assertEqual(native.frames, [])
            for _ in range(10):
                env.advance()
            self.assertEqual(len(native.queued_frames), 10)
            self.assertEqual(native.queued_frames, expected)
            self.assertEqual(trajectory.source['native_function_calls'], 1)
            self.assertEqual(trajectory.source['frame_count'], 10)
            np.testing.assert_allclose(native.positions['left'], action['left_arm_joint_state'])

    def test_native_generation_does_not_consume_support_or_lose_it_on_new_goal(self):
        with patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE='native'):
            native = PhysicsFixture()
            native.robot_manager.robot_list.append(SimpleNamespace(type='support', arm_name='support_arm',
                gripper_name='support_ee', ee_type='gripper'))
            support = [{'support_arm': {'position': [float(i)]}} for i in range(8)]
            native.support_arm_action = [deepcopy(support)]
            env = TimedJointEnvironment(native)
            env.set_joint_goal(native_action(.8), self.trajectory(native, grip=1.))
            self.assertEqual(native.support_arm_action[0], support)
            for _ in range(3):
                env.advance()
            self.assertEqual(native.support_arm_action[0], support[3:])
            env.set_joint_goal(native_action(.2), self.trajectory(native, .2, 1.))
            self.assertEqual(native.support_arm_action[0], support[3:])
            for _ in range(5):
                env.advance()
            self.assertEqual([f['support_arm'] for f in native.queued_frames],
                             [f['support_arm'] for f in support])

    def test_native_recorded_normalized_actions_replay_the_same_filtered_controls(self):
        with patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE='native'):
            live, replay = PhysicsFixture(), PhysicsFixture()
            env, playback = TimedJointEnvironment(live), TimedJointEnvironment(replay)
            env.set_joint_goal(native_action(.8, 0.), self.trajectory(live))
            for _ in range(35):
                reference = env.advance()
                playback.take_action(reference)
                for arm in ('left', 'right'):
                    np.testing.assert_allclose(live.positions[arm], replay.positions[arm], atol=1e-14)
                    np.testing.assert_allclose(live.openings[arm], replay.openings[arm], atol=1e-14)

    def test_all_profiles_retain_native_horizon_rewards_and_truthful_feedback(self):
        for profile in PROFILE_NAMES:
            with self.subTest(profile=profile), patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE=profile):
                native = RateLimitedPhysics(task_steps=2)
                result, _, _ = run_goal(native, command(.8))
                self.assertEqual(result['step'], 20)
                self.assertEqual(native.reward_ticks, [10, 20])
                self.assertEqual(result['action_feedback']['status'], 'episode_ended')
                self.assertEqual(result['action_feedback']['trajectory']['profile'], PROFILE_NAMES[profile])
                self.assertEqual(result['action_feedback']['goal_submissions'], 1)

    def test_linear_and_quintic_declared_bounds_and_partial_targets_differ_honestly(self):
        for profile, endpoint, acceleration in [('linear', .42, False), ('quintic', .224, True)]:
            with self.subTest(profile=profile), patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE=profile):
                settings = controller_settings()
                trajectory = self.trajectory(PhysicsFixture(), 1.4, 1.)
                self.assertAlmostEqual(trajectory.targets['left'][0], endpoint)
                self.assertLess(trajectory.goal_fraction, 1.)
                samples = np.array([trajectory.positions(i)['left'] for i in range(trajectory.duration_ticks + 1)])
                self.assertLessEqual(np.max(np.abs(np.diff(samples, axis=0))) * 250, .6 + 1e-10)
                self.assertEqual(settings['reference_acceleration_limit_enforced'], acceleration)
        with patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE='native'):
            self.assertFalse(controller_settings()['reference_velocity_limit_enforced'])

    def test_system_prompt_is_identical_across_profiles_and_invalid_profile_is_rejected(self):
        prompts = []
        for profile in PROFILE_NAMES:
            with patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE=profile):
                prompts.append(build_system([]))
        self.assertEqual(len(set(prompts)), 1)
        with patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE='unknown'):
            with self.assertRaises(ValueError):
                selected_profile()


if __name__ == '__main__':
    unittest.main()
