"""Check reference bounds and synchronization independently of actuator dynamics."""
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE / 'harness')]
from egg_harness.joint_goal import controller_settings
from egg_harness.joint_trajectory import JointTrajectory


class TrajectoryTests(unittest.TestCase):
    def make_trajectory(self, delta):
        start = np.array([.2, -.1, .3, -.4, .5, -.6])
        plan = {arm: dict(start=start.copy(), target=start + sign * np.asarray(delta))
                for arm, sign in [('left', 1), ('right', -.5)]}
        return JointTrajectory.from_plan(plan, controller_settings())

    def test_speed_acceleration_and_zero_endpoint_velocity_for_short_and_long_moves(self):
        settings = controller_settings()
        for distance in (0., .0001, .01, .12, .6, 2.):
            with self.subTest(distance=distance):
                trajectory = self.make_trajectory([distance, -distance / 3, 0., distance / 2, 0., 0.])
                positions = np.array([trajectory.positions(tick)['left']
                                      for tick in range(-1, trajectory.duration_ticks + 3)])
                velocities = np.diff(positions, axis=0) * 250
                accelerations = np.diff(velocities, axis=0) * 250
                self.assertLessEqual(np.abs(velocities).max(), settings['joint_velocity_limit_rad_s'] + 1e-9)
                self.assertLessEqual(np.abs(accelerations).max(), settings['joint_acceleration_limit_rad_s2'] + 1e-8)
                np.testing.assert_array_equal(velocities[0], np.zeros(6))
                np.testing.assert_allclose(velocities[-1], np.zeros(6), atol=1e-12)
                np.testing.assert_allclose(trajectory.positions(trajectory.duration_ticks)['left'], trajectory.targets['left'])
                self.assertLessEqual(trajectory.duration_ticks / 250 + settings['settle_window_seconds'],
                                     settings['max_motion_seconds'] + 1e-12)

    def test_all_joints_and_both_arms_follow_one_line_from_nonzero_start(self):
        trajectory = self.make_trajectory([.6, -.3, .15, -.2, .1, -.4])
        for tick in range(trajectory.duration_ticks + 1):
            position = trajectory.positions(tick)
            left_delta = position['left'] - trajectory.starts['left']
            right_delta = position['right'] - trajectory.starts['right']
            np.testing.assert_allclose(right_delta, -.5 * left_delta, atol=1e-12)
            np.testing.assert_allclose(left_delta, left_delta[0] * np.array([1., -.5, .25, -1/3, 1/6, -2/3]), atol=1e-12)
        self.assertLess(trajectory.goal_fraction, 1.)
        self.assertGreater(trajectory.goal_fraction, 0.)

    def test_short_goal_reaches_exact_target_and_stationary_goal_never_moves(self):
        short = self.make_trajectory([.01, 0., 0., 0., 0., 0.])
        self.assertEqual(short.goal_fraction, 1.)
        np.testing.assert_allclose(short.targets['left'] - short.starts['left'], [.01, 0., 0., 0., 0., 0.])
        still = self.make_trajectory([0.] * 6)
        self.assertEqual(still.duration_ticks, 0)
        for tick in (0, 1, 25, 200):
            for arm in ('left', 'right'):
                np.testing.assert_array_equal(still.positions(tick)[arm], still.starts[arm])


if __name__ == '__main__':
    unittest.main()
