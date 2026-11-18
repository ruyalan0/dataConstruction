"""Keep the reduced push T reference velocity isolated from all other tasks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE / 'harness'), str(HERE / 'tests')]
from egg_harness.control_timing import timing_record
from egg_harness.joint_goal import controller_settings
from test_joint_goal import RateLimitedPhysics, command, run_goal


class PushTVelocityTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE='linear',
                                 ASTRA_CONTROLLER_TASK='')
        environment.start()
        self.addCleanup(environment.stop)

    def test_push_t_velocity_matches_other_tasks(self):
        baseline = controller_settings()
        self.assertEqual(baseline['joint_velocity_limit_rad_s'], 3.75)
        expected = dict(baseline)
        self.assertEqual(controller_settings('push_T'), expected)
        config = json.loads((HERE / 'task-configs/push_T.json').read_text())
        self.assertEqual(config['goal_controller'], expected)
        for task in ('general_pickup', 'play_tic_tac_toe', 'put_bottles_into_dustbin',
                     'stack_bowls', 'stack_blocks', 'classify_objects'):
            with self.subTest(task=task):
                self.assertEqual(controller_settings(task), baseline)

    def test_process_context_selects_push_t_without_leaking_to_other_tasks(self):
        baseline = controller_settings()
        with patch.dict(os.environ, ASTRA_CONTROLLER_TASK='push_T'):
            self.assertEqual(controller_settings(), controller_settings('push_T'))
            self.assertEqual(timing_record()['goal_controller'], controller_settings('push_T'))
            self.assertEqual(controller_settings('stack_bowls'), baseline)
        self.assertEqual(controller_settings(), baseline)

    def test_fresh_policy_and_simulator_processes_receive_matching_settings(self):
        program = (
            'import json; '
            'from egg_harness.joint_goal import controller_settings; '
            'from egg_harness.control_timing import timing_record; '
            'print(json.dumps([controller_settings(), timing_record()["goal_controller"]]))'
        )
        for task, velocity in [('push_T', 3.75), ('classify_objects', 3.75)]:
            with self.subTest(task=task):
                environment = dict(os.environ, PYTHONPATH=str(HERE / 'harness'),
                                   ASTRA_CONTROLLER_TASK=task, PYTHONDONTWRITEBYTECODE='1')
                result = subprocess.check_output([sys.executable, '-B', '-c', program],
                                                 env=environment, text=True)
                settings, timing = json.loads(result)
                self.assertEqual(settings, timing)
                self.assertEqual(settings['joint_velocity_limit_rad_s'], velocity)

    def test_push_t_execution_bounds_both_arms_at_common_velocity(self):
        request = command(1.)
        request['action']['arms']['right'] = dict(control='joint',
            joints=[-0.5, 0., 0., 0., 0., 0.], gripper=1.)
        with patch.dict(os.environ, ASTRA_CONTROLLER_TASK='push_T'):
            result, actions, _ = run_goal(RateLimitedPhysics(speed=5.), request)
        feedback = result['action_feedback']
        self.assertEqual(feedback['controller']['joint_velocity_limit_rad_s'], 3.75)
        self.assertEqual(feedback['status'], 'target_reached')
        self.assertIsNone(feedback['reason'])
        self.assertAlmostEqual(feedback['trajectory']['goal_fraction'], 1.)
        self.assertAlmostEqual(feedback['trajectory']['duration_seconds'], 0.268)
        self.assertTrue(actions)
        for arm in ('left', 'right'):
            positions = np.array([[0.] * 6] + [
                row['action'][arm + '_arm_joint_state'] for row in actions])
            self.assertLessEqual(np.max(np.abs(np.diff(positions, axis=0))) * 250,
                                 3.75 + 1e-10)

    def test_quintic_preserves_acceleration_and_settling_at_common_velocity(self):
        with patch.dict(os.environ, ASTRA_CONTROLLER_TASK='push_T',
                        ASTRA_INTERPOLATION_PROFILE='quintic'):
            settings = controller_settings()
            result, actions, _ = run_goal(RateLimitedPhysics(speed=5.), command(1.))
        feedback = result['action_feedback']
        self.assertEqual(settings['joint_velocity_limit_rad_s'], 3.75)
        self.assertEqual(settings['joint_acceleration_limit_rad_s2'], 3.)
        self.assertEqual(settings['settle_window_seconds'], 0.1)
        self.assertTrue(settings['reference_acceleration_limit_enforced'])
        self.assertEqual(feedback['trajectory']['profile'], 'synchronized_quintic')
        self.assertEqual(feedback['reason'], 'trajectory_segment_complete')
        self.assertAlmostEqual(feedback['trajectory']['duration_seconds'], 0.7)
        self.assertAlmostEqual(feedback['actual_duration_seconds'], 0.8)
        self.assertGreater(feedback['trajectory']['goal_fraction'], 0.224)
        self.assertTrue(actions)


if __name__ == '__main__':
    unittest.main()
