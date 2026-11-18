"""Check pouring retains its original linear task settings and references."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness'), str(HERE / 'tests')]
from run_local import resolve_interpolation_profile
from egg_harness.control_timing import timing_record
from egg_harness.joint_goal import controller_settings
from test_joint_goal import RateLimitedPhysics, command, run_goal


class PourLiquidProfileTests(unittest.TestCase):
    def test_pour_defaults_to_original_linear_and_retains_recorded_task_settings(self):
        source = HERE.parents[1] / 'runs/sol61-f-additional-seven-tasks-L20-L22-20261109-215604/configs/sol/linear/pour_liquid_into_cup.json'
        before = json.loads(source.read_text())
        config_path = HERE / 'task-configs/pour_liquid_into_cup.json'
        config = json.loads(config_path.read_text())
        self.assertEqual({key for key in before.keys() | config.keys() if before.get(key) != config.get(key)},
                         {'io_variant'})
        self.assertEqual(config['task'], 'pour_liquid_into_cup')
        self.assertEqual(config['io_variant'], HERE.name)
        for name in config['skill_names']:
            self.assertTrue((HERE / 'harness/skills' / name / 'SKILL.md').is_file(), name)
        with patch.dict(os.environ):
            os.environ.pop('ASTRA_INTERPOLATION_PROFILE', None)
            self.assertEqual(resolve_interpolation_profile(config['task']), 'linear')
            self.assertEqual(resolve_interpolation_profile(config['task'], task_config=config_path), 'linear')
            for task in ('insert_tubes', 'put_bottles_into_dustbin', 'general_pickup'):
                self.assertEqual(resolve_interpolation_profile(task), 'linear')
        with patch.dict(os.environ, ASTRA_INTERPOLATION_PROFILE='linear'):
            self.assertEqual(config['goal_controller'], controller_settings(config['task']))

    def test_task_profile_executes_linear_without_push_t_stages(self):
        with patch.dict(os.environ):
            os.environ.pop('ASTRA_INTERPOLATION_PROFILE', None)
            os.environ['ASTRA_INTERPOLATION_PROFILE'] = resolve_interpolation_profile('pour_liquid_into_cup')
            os.environ['ASTRA_CONTROLLER_TASK'] = 'pour_liquid_into_cup'
            settings = controller_settings()
            self.assertEqual(timing_record()['goal_controller'], settings)
            request = command(.12)
            request['action']['arms']['right'] = dict(control='joint',
                joints=[-.06, 0., 0., 0., 0., 0.], gripper=1.)
            result, actions, _ = run_goal(RateLimitedPhysics(speed=5.), request)
        feedback = result['action_feedback']
        self.assertEqual(feedback['trajectory']['profile'], 'linear_auto_duration')
        self.assertEqual(feedback['status'], 'target_reached')
        self.assertNotIn('motion_stage', feedback)
        self.assertTrue(settings['reference_velocity_limit_enforced'])
        self.assertFalse(settings['reference_acceleration_limit_enforced'])
        self.assertTrue(actions)
        for arm in ('left', 'right'):
            positions = np.array([[0.] * 6] + [row['action'][arm + '_arm_joint_state'] for row in actions])
            velocities = np.diff(positions, axis=0) * 250
            self.assertLessEqual(np.max(np.abs(velocities)), settings['joint_velocity_limit_rad_s'] + 1e-9)
        np.testing.assert_allclose([row['action']['right_arm_joint_state'][0] for row in actions],
                                   [-.5 * row['action']['left_arm_joint_state'][0] for row in actions], atol=1e-12)


if __name__ == '__main__':
    unittest.main()
