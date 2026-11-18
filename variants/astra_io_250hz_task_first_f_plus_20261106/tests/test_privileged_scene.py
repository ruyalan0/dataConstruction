"""Check live geometry, coordinate frames and propagation to teacher requests."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE / 'harness'), str(HERE / 'tests')]
from egg_harness.privileged_scene import capture_privileged_scene, validate_privileged_scene, empty_scene
from egg_harness.observation_pair import build_pair, CAMERAS
from test_transport import context
from privileged_fixture import attach_scene


class PrivilegedSceneTests(unittest.TestCase):
    def environment(self, origin=(0., 0., 0.)):
        env = SimpleNamespace(take_action_cnt=[83])
        manager = attach_scene(env, origin)
        return env, manager

    def test_live_pose_points_and_geometry_use_the_same_world_frame(self):
        env, manager = self.environment((10., 20., 30.))
        scene = capture_privileged_scene(env, 83)
        obj = scene['objects']['target']
        np.testing.assert_allclose(obj['position_world'], [10.1, 20.2, 30.3])
        np.testing.assert_allclose(obj['bbox']['center_world'], obj['position_world'])
        np.testing.assert_allclose(obj['bbox']['extent_world'], [.2, .4, .6])
        np.testing.assert_allclose(obj['functional_points']['active']['grasp']['poses_world'][0]['position_world'],
                                   [10.1, 20.2, 30.35])
        np.testing.assert_allclose(obj['support_points']['active']['top']['poses_world'][0]['position_world'],
                                   [10.1, 20.2, 30.6])
        self.assertEqual(env.take_action_cnt, [83])
        manager.position += [1., 0., 0.]
        current = capture_privileged_scene(env, 83)
        self.assertAlmostEqual(current['objects']['target']['position_world'][0], 11.1)
        self.assertAlmostEqual(scene['objects']['target']['position_world'][0], 10.1)

    def test_bounding_box_applies_current_rotation_and_nonuniform_scale(self):
        env, manager = self.environment()
        manager.scale = np.array([2., 1., 1.])
        manager.quaternion = Rotation.from_euler('z', 90, degrees=True).as_quat()[[3, 0, 1, 2]]
        scene = capture_privileged_scene(env, 83)
        obj = scene['objects']['target']
        np.testing.assert_allclose(obj['bbox']['extent_world'], [.4, .4, .6])
        np.testing.assert_allclose(obj['bbox']['min_world'], [-.1, 0., 0.], atol=1e-12)
        self.assertIn('functional_points.active.grasp', obj['unavailable'])
        self.assertNotIn('functional_points', obj)

    def test_missing_geometry_is_explicit_and_does_not_erase_valid_poses(self):
        env, manager = self.environment()
        manager.metadata.pop('geometry')
        scene = capture_privileged_scene(env, 83)
        obj = scene['objects']['target']
        self.assertIn('bbox', obj['unavailable'])
        self.assertIn('position_world', obj)
        self.assertNotIn('bbox', obj)

    def test_deformable_box_comes_from_current_mesh_instead_of_root_asset_box(self):
        env, manager = self.environment()
        manager.instance_type_by_env[0]['target_instance'] = 'garment'
        manager.obj.sample_mesh_vertices = lambda: (np.array([[2., 3., 4.], [3., 5., 4.1]]), None, None, None)
        obj = capture_privileged_scene(env, 83)['objects']['target']
        np.testing.assert_allclose(obj['bbox']['center_world'], [2.5, 4., 4.05])
        self.assertEqual(obj['bbox']['source'], 'current_mesh_world_axis_aligned_bbox')

    def test_stale_capture_nonfinite_geometry_and_missing_scene_are_rejected(self):
        env, _ = self.environment()
        with self.assertRaisesRegex(RuntimeError, 'RGB capture step'):
            capture_privileged_scene(env, 82)
        scene = capture_privileged_scene(env, 83)
        with self.assertRaisesRegex(ValueError, 'stale'):
            validate_privileged_scene(scene, 84)
        scene['objects']['target']['position_world'][0] = float('nan')
        with self.assertRaisesRegex(ValueError, 'Non-finite'):
            validate_privileged_scene(scene, 83)
        with self.assertRaisesRegex(ValueError, 'privileged scene'):
            validate_privileged_scene(None, 83)

    def test_teacher_payload_keeps_scene_and_quantizes_only_the_sent_copy(self):
        env, manager = self.environment()
        manager.position[0] = .123456789
        source = context(83)
        source['privileged_scene'] = capture_privileged_scene(env, 83)
        before = deepcopy(source)
        payload, _, _ = build_pair(source, {name: name + '.jpg' for name in CAMERAS})
        self.assertEqual(source, before)
        self.assertEqual(payload['privileged_scene']['objects']['target']['position_world'][0], .1235)
        self.assertEqual(payload['privileged_scene']['objects']['target']['category'], 'test_object')
        self.assertEqual(payload['privileged_scene']['step'], payload['observation']['step'])
        source['privileged_scene']['step'] = 82
        with self.assertRaisesRegex(ValueError, 'stale'):
            build_pair(source, {name: name + '.jpg' for name in CAMERAS})


if __name__ == '__main__':
    unittest.main()
