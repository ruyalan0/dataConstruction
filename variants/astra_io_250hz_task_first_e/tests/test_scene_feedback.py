from copy import deepcopy
from itertools import product
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE / 'harness'), str(HERE / 'tests')]
from egg_harness.scene_feedback import SceneFeedback, classification_progress, surface_geometry, validate_scene_feedback
from egg_harness.privileged_scene import capture_privileged_scene
from egg_harness.observation_pair import build_pair, CAMERAS
from privileged_fixture import attach_scene
from test_transport import context


class SceneFeedbackTests(unittest.TestCase):
    def fixture(self):
        env = SimpleNamespace(take_action_cnt=[0])
        manager = attach_scene(env)
        manager.position = np.array([.138, 0., 1.])
        state = {}
        for arm in ('left', 'right'):
            state[arm + '_ee_pose'] = [0., 0., 1., 1., 0., 0., 0.]
            state[arm + '_arm_joint_state'] = [0.] * 6
            state[arm + '_ee_joint_state'] = [0.]
            state[arm + '_gripper_measured_joints'] = [.02, .02]
        return env, manager, state

    def test_static_proximity_does_not_claim_retention(self):
        env, manager, state = self.fixture()
        tracker = SceneFeedback()
        for step in [0, 10, 20, 30]:
            env.take_action_cnt[0] = step
            tracker.sample(env, state)
        result = tracker.movement('target', 'left')
        self.assertEqual(result['status'], 'uncertain')
        self.assertFalse(result['secure_grasp_verified'])
        self.assertEqual(env.take_action_cnt[0], 30)

    def test_co_motion_and_later_slip_use_simulation_history(self):
        env, manager, state = self.fixture()
        tracker = SceneFeedback()
        for step in [0, 10, 20, 30]:
            env.take_action_cnt[0] = step
            state['left_ee_pose'][2] = 1. + step * .001
            manager.position[2] = state['left_ee_pose'][2]
            tracker.sample(env, state)
        result = tracker.movement('target', 'left')
        self.assertEqual(result['status'], 'moves_with_gripper')
        self.assertAlmostEqual(result['object_velocity_world_m_s'][2], .25)
        env.take_action_cnt[0] = 40
        state['left_ee_pose'][2] += .04
        tracker.sample(env, state)
        result = tracker.movement('target', 'left')
        self.assertEqual(result['status'], 'relative_motion_detected')
        self.assertTrue(result['possible_slip'])

    def test_object_motion_without_wrist_motion_is_not_a_grasp(self):
        env, manager, state = self.fixture()
        tracker = SceneFeedback()
        for step in [0, 10, 20, 30]:
            env.take_action_cnt[0] = step
            manager.position[2] += .01
            tracker.sample(env, state)
        self.assertEqual(tracker.movement('target', 'left')['status'], 'uncertain')

    def test_capture_is_same_step_detached_and_survives_missing_mesh(self):
        env, manager, state = self.fixture()
        tracker = SceneFeedback()
        scene = capture_privileged_scene(env, 0)
        result = tracker.capture(env, scene, state, 'Pick up the target.')
        self.assertEqual(result['step'], 0)
        self.assertFalse(result['objects']['target']['contacts']['available'])
        self.assertEqual(result['objects']['target']['geometry']['source'], 'unavailable')
        result['objects'].clear()
        self.assertIn('target', tracker.capture(env, scene, state, '')['objects'])
        self.assertEqual(env.take_action_cnt, [0])
        with self.assertRaisesRegex(ValueError, 'same step'):
            tracker.capture(env, dict(scene, step=10), state, '')

    def test_geometry_uses_current_world_mesh_and_marks_candidate_unverified(self):
        points = np.array(list(product([1., 1.04], [2., 2.12], [3., 3.02])))
        obj = SimpleNamespace(sample_mesh_vertices=lambda: (points, None, None, None))
        geometry = surface_geometry(obj, dict(object_type='rigid'))
        np.testing.assert_allclose(geometry['min_world'], [1., 2., 3.])
        candidate = geometry['grasp_candidate']
        self.assertAlmostEqual(candidate['width_m'], .04)
        np.testing.assert_allclose(candidate['center_world'], [1.02, 2.06, 3.01])
        self.assertIsNone(candidate['collision_free'])
        self.assertFalse(candidate['force_closure_verified'])
        self.assertIsNone(surface_geometry(obj, dict(object_type='garment'))['grasp_candidate'])

    def test_classification_requires_entire_category_and_rejects_contamination(self):
        env, manager, state = self.fixture()
        for arm in ('left', 'right'):
            state[arm + '_gripper_measured_joints'] = [.044, .044]
        objects = {}
        for index, category in enumerate(['pepper', 'chocolate_bar', 'watch']):
            center = index * .4
            objects[f'basket{index}'] = dict(bbox=dict(vertices_world=[
                [center + horizontal, depth, height] for horizontal, depth, height in product([-.1, .1], [-.1, .1], [.7, .8])]))
            for item in range(2):
                objects[f'item{index}_{item}'] = dict(category=category, object_type='rigid',
                    position_world=[center, 0. if item == 0 else -.3, .75])
        instruction = 'Put pepper objects into the left basket, chocolate_bar objects into the middle basket, and watch objects into the right basket, then reset the robot arm.'
        progress = classification_progress(dict(objects=objects), instruction, state)
        self.assertTrue(all(not category['geometry_complete'] for category in progress['categories']))
        objects['item0_1']['position_world'][1] = .02
        progress = classification_progress(dict(objects=objects), instruction, state)
        self.assertTrue(progress['categories'][0]['geometry_complete'])
        objects['item1_1']['position_world'] = [0., .01, .75]
        progress = classification_progress(dict(objects=objects), instruction, state)
        self.assertFalse(progress['categories'][0]['geometry_complete'])
        self.assertEqual(progress['categories'][0]['contaminants'], ['item1_1'])
        self.assertFalse(progress['evaluator_queried'])

    def test_model_payload_preserves_feedback_and_rejects_stale_values(self):
        source = context(83)
        source['scene_feedback'] = dict(schema='linear_scene_feedback_v1', step=83, objects={},
                                        home_joint_error_rad=dict(left=.123456789))
        before = deepcopy(source)
        payload, _, _ = build_pair(source, {name: name for name in CAMERAS})
        self.assertEqual(source, before)
        self.assertEqual(payload['scene_feedback']['home_joint_error_rad']['left'], .1235)
        source['scene_feedback']['step'] = 82
        with self.assertRaisesRegex(ValueError, 'stale'):
            build_pair(source, {name: name for name in CAMERAS})
        with self.assertRaisesRegex(ValueError, 'Nonfinite'):
            validate_scene_feedback(dict(schema='linear_scene_feedback_v1', step=0, value=float('nan')), 0)


if __name__ == '__main__':
    unittest.main()
