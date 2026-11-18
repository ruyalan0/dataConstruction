"""Verify the align_blocks speed cap in executed references and child processes."""
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
from egg_harness.joint_goal import controller_settings
from test_joint_goal import RateLimitedPhysics, command, run_goal


class AlignBlocksVelocityTests(unittest.TestCase):
    def test_task_config_and_other_task_limits(self):
        with patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE='linear', ASTRA_CONTROLLER_TASK=''):
            config = json.loads((HERE / 'task-configs/align_blocks.json').read_text())
            self.assertEqual(config['goal_controller'], controller_settings('align_blocks'))
            self.assertEqual(config['goal_controller']['joint_velocity_limit_rad_s'], 1.2)
            for task in ('push_T', 'general_pickup', 'insert_tubes', 'pour_liquid_into_cup',
                         'put_bottles_into_dustbin', 'stack_bowls'):
                self.assertEqual(controller_settings(task)['joint_velocity_limit_rad_s'], 3.75)

    def test_fresh_process_timing_matches_controller(self):
        program = ('import json; from egg_harness.joint_goal import controller_settings; '
                   'from egg_harness.control_timing import timing_record; '
                   'print(json.dumps([controller_settings(), timing_record()["goal_controller"]]))')
        environment = dict(os.environ, PYTHONPATH=str(HERE / 'harness'),
                           ASTRA_CONTROLLER_TASK='align_blocks', ASTRA_INTERPOLATION_PROFILE='linear',
                           PYTHONDONTWRITEBYTECODE='1')
        settings, timing = json.loads(subprocess.check_output(
            [sys.executable, '-B', '-c', program], env=environment, text=True))
        self.assertEqual(settings, timing)
        self.assertEqual(settings['joint_velocity_limit_rad_s'], 1.2)

    def test_both_arms_obey_cap_with_native_clock_and_bounded_segments(self):
        request = command(1.)
        request['action']['arms']['right'] = dict(control='joint', joints=[-.5, 0., 0., 0., 0., 0.], gripper=1.)
        native = RateLimitedPhysics(speed=5.)
        with patch.dict(os.environ, ASTRA_CONTROLLER_TASK='align_blocks', ASTRA_INTERPOLATION_PROFILE='linear'):
            result, actions, _ = run_goal(native, request)
        feedback = result['action_feedback']
        self.assertEqual(feedback['controller']['joint_velocity_limit_rad_s'], 1.2)
        self.assertEqual(feedback['reason'], 'trajectory_segment_complete')
        self.assertFalse(feedback['target_reached'])
        self.assertLessEqual(feedback['actual_duration_seconds'], 0.8)
        self.assertEqual(native.reward_ticks, list(range(10, result['step'] + 1, 10)))
        self.assertTrue(actions)
        for arm in ('left', 'right'):
            positions = np.array([[0.] * 6] + [row['action'][arm + '_arm_joint_state'] for row in actions])
            self.assertLessEqual(np.max(np.abs(np.diff(positions, axis=0))) * 250, 1.2 + 1e-10)


if __name__ == '__main__':
    unittest.main()
