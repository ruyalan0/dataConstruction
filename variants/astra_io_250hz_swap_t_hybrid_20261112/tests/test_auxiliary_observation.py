"""The idle wrist is usable through the actual joint tool without releasing a grasp."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness')]
from egg_harness.auxiliary_observation import observer_context, with_observer_skill
from egg_harness.native_tools import ToolRegistry
from egg_harness.observation_pair import build_pair, CAMERAS
from egg_harness.prompting import build_system
from test_joint_goal import RateLimitedPhysics, run_goal
from test_transport import context


class AuxiliaryObservationTests(unittest.TestCase):
    def test_camera_mapping_reaches_model_and_open_fingers_do_not_certify_empty(self):
        source = context()
        source['state']['left_gripper_measured_joints'] = [.044, .044]
        grips = dict(left=1., right=0.)
        source['auxiliary_observation'] = observer_context(source['state'], grips)
        payload, images, _ = build_pair(source, {name: name + '.jpg' for name in CAMERAS})
        arms = payload['observation']['auxiliary_observation']['arms']
        self.assertEqual(arms['left']['camera'], 'cam_left_wrist')
        self.assertEqual(arms['right']['camera'], 'cam_right_wrist')
        self.assertTrue(arms['left']['open_hand_candidate'])
        self.assertFalse(arms['right']['open_hand_candidate'])
        self.assertFalse(arms['left']['empty_hand_confirmed'])
        self.assertEqual(set(images), {'current_' + name for name in CAMERAS})

    def test_observer_skill_is_added_once_even_with_old_task_configuration(self):
        names = ['visual-contact-control', 'general-pickup']
        result = with_observer_skill(names)
        self.assertEqual(names, ['visual-contact-control', 'general-pickup'])
        self.assertEqual(result[-1], 'idle-arm-observation')
        self.assertIn('cross-side-handoff', result)
        self.assertEqual(with_observer_skill(result), result)
        system = build_system([(name, (HERE / 'harness/skills' / name / 'SKILL.md').read_text()) for name in result])
        self.assertIn('Task guidance: idle-arm-observation', system)
        self.assertIn('Task guidance: cross-side-handoff', system)
        self.assertIn('cam_left_wrist', system)
        self.assertIn('working arm to null', system)

    def test_native_single_observer_action_holds_working_joints_and_both_gripper_commands(self):
        native = RateLimitedPhysics(speed=2.)
        native.openings['right'] = [.013, .013]
        native.robot_manager.control_manager.prev_control[0]['right_ee_joint_state']['position'] = [.013, .013]
        before = native.positions['right'].copy()
        registry = ToolRegistry(dict(action_interface='joint'))
        args = dict(assessment='Working wrist view occluded; left hand is visibly empty.',
                    plan='Move only the left wrist to inspect the right grasp.', memory='Right hand retains object.',
                    left=dict(control='joint', joints=[.1, 0., 0., 0., 0., 0.], gripper=None), right=None)
        decision = registry.decision('control_arms', json.dumps(args))
        request = dict(id='left-observer-only', op='control_action', phase='transit',
                       action=decision['action']['control_arms'])
        grips = dict(left=1., right=0.)
        result, actions, _ = run_goal(native, request, grips)
        self.assertGreater(len(actions), 0)
        for row in actions:
            np.testing.assert_array_equal(row['action']['right_arm_joint_state'], before)
            self.assertEqual(row['action']['right_ee_joint_state'], [0.])
            self.assertEqual(row['action']['left_ee_joint_state'], [1.])
        self.assertTrue(result['action_feedback']['pose_validation']['accepted'])
        np.testing.assert_array_equal(result['state']['right_arm_joint_state'], before)
        self.assertEqual(grips, dict(left=1., right=0.))


if __name__ == '__main__':
    unittest.main()
