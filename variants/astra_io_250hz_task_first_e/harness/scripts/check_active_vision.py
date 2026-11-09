"""Temporal geometry, immutable evidence, stationary assumptions and policy routing."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

from egg_harness import native_responses, skill_policy
from egg_harness.active_vision import locate_temporal
from egg_harness.multiview import MultiViewGeometry, project
from egg_harness.native_tools import ToolRegistry
from egg_harness.public_review import review_frames
from scripts.check_native_tools import Client, META, call, response


class ActiveVisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.episode = self.root / 'episode'
        self.frames = self.episode / 'frames'; self.frames.mkdir(parents=True)
        mount = np.eye(4); mount[:3, :3] = Rotation.from_euler('y', 20, degrees=True).as_matrix(); mount[:3, 3] = [.05, 0, .01]
        camera = {'intrinsic': [[400., 0, 320], [0, 400., 240], [0, 0, 1]], 'resolution': [640, 480]}
        rig = {'schema_version': 1, 'status': 'render_qualified', 'cameras': {
            'cam_head': {**camera, 'world_from_camera': np.eye(4).tolist()},
            'cam_left_wrist': {**camera, 'ee_from_camera': mount.tolist()},
            'cam_right_wrist': {**camera, 'ee_from_camera': mount.tolist()}}}
        path = self.root / 'calibration.json'; path.write_text(json.dumps(rig)); self.geometry = MultiViewGeometry(path)
        self.point = np.array([0., .005, -.30]); self.camera = 'cam_left_wrist'; self.observations = []
        for step in range(5):
            t = np.eye(4); t[:3, :3] = Rotation.from_euler('z', step * 2, degrees=True).as_matrix(); t[0, 3] = -.04 + step * .02
            ee = t @ np.linalg.inv(mount)
            pose = [*ee[:3, 3], *Rotation.from_matrix(ee[:3, :3]).as_quat()[[3, 0, 1, 2]]]
            state = {'left_ee_pose': pose, 'right_ee_pose': [.4, 0, 0, 1, 0, 0, 0]}
            for arm in ('left', 'right'):
                state[arm + '_ee_joint_state'] = [1.]; state[arm + '_gripper_measured_joints'] = [.044, .044]
            cameras = {}
            for name in rig['cameras']:
                picture = self.frames / f'image-{step}-{name}.jpg'
                Image.new('RGB', (640, 480), (step * 30, 50, 60)).save(picture); cameras[name] = str(picture)
            obs = {'step': step, 'state': state, 'cameras': cameras, 'camera_calibration_id': self.geometry.calibration_id,
                   'ended': False, 'instruction': 'Stationary fixture', 'budget': {}}
            self.observations.append(obs); self.save(step)
        self.obs = self.observations[-1]
        self.request = {'step': 4, 'camera': self.camera, 'feature': 'One visible fixed corner',
            'stationary_target': True, 'camera_hand_empty': True,
            'stationarity_evidence': 'Target is fixed in the scene, clear of the open empty camera hand; other arm holds.',
            'samples': [{'step': step, 'pixel': self.pixel(step)} for step in (0, 4)]}

    def save(self, step):
        name = 'initial' if step == 0 else f'step-{step:04d}'
        (self.frames / (name + '.json')).write_text(json.dumps(self.observations[step]))

    def pixel(self, step, point=None):
        return project(self.point if point is None else point, self.geometry.cameras(self.observations[step])[self.camera]).tolist()

    def evidence(self, request=None):
        request = request or self.request
        images, records = review_frames(self.episode, 4, [{'step': s['step'], 'camera': self.camera} for s in request['samples']])
        manifest = {name: {'path': path, 'size': [640, 480], 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()} for name, path in images.items()}
        return records, manifest

    def locate(self, request=None, evidence=None):
        request = request or self.request
        records, manifest = evidence or self.evidence(request)
        return locate_temporal(self.geometry, request, self.episode, self.obs, records, manifest)

    def test_actual_pose_and_mount_two_three_frames(self):
        for steps in ((0, 4), (0, 2, 4)):
            request = {**self.request, 'samples': [{'step': s, 'pixel': self.pixel(s)} for s in steps]}
            result = self.locate(request)
            np.testing.assert_allclose(result['position'], self.point, atol=1e-9)
            self.assertEqual(result['native_steps_executed'], 0)
            self.assertAlmostEqual(max(result['measured_camera_baselines_m']), .08)
            self.assertFalse(result['stationarity']['verified'])

    def test_step_camera_and_assumption_contract(self):
        evidence = self.evidence()
        for change in ({'step': 3}, {'step': True}, {'camera': 'cam_head'}, {'stationary_target': False},
                       {'camera_hand_empty': False}, {'stationarity_evidence': '   '},
                       {'samples': [{'step': s, 'pixel': [320, 240]} for s in (4, 0)]},
                       {'samples': [{'step': s, 'pixel': [320, 240]} for s in (0, 0, 4)]},
                       {'samples': [{'step': s, 'pixel': [320, 240]} for s in (0, 3)]},
                       {'samples': [{'step': s, 'pixel': [320, 240]} for s in (0, 5)]}):
            with self.subTest(change=change), self.assertRaises(ValueError): self.locate({**self.request, **change}, evidence)
        with self.assertRaises(ValueError):
            locate_temporal(self.geometry, {**self.request, 'step': 30, 'samples': [{'step': s, 'pixel': [320, 240]} for s in (0, 30)]}, self.episode, {**self.obs, 'step': 30}, *evidence)

    def test_review_and_actual_sent_bytes_required(self):
        records, manifest = self.evidence()
        for rows, sent in (([], manifest), (records, {}), (records, {**manifest, records[0]['image']: {**manifest[records[0]['image']], 'sha256': 'bad'}})):
            with self.assertRaises(ValueError): self.locate(evidence=(rows, sent))
        path = Path(self.observations[0]['cameras'][self.camera]); Image.new('RGB', (640, 480), 'white').save(path)
        with self.assertRaisesRegex(ValueError, 'changed since review'): self.locate(evidence=(records, manifest))

    def test_pose_mutation_and_cross_episode_rejected(self):
        evidence = self.evidence()
        self.observations[0]['state']['left_ee_pose'][0] += .001; self.save(0)
        with self.assertRaisesRegex(ValueError, 'changed since review'): self.locate(evidence=evidence)
        self.observations[0]['cameras'][self.camera] = str(self.root / 'outside.jpg'); self.save(0)
        with self.assertRaises(ValueError): self.locate(evidence=evidence)

    def test_endpoint_state_and_presented_resolution_bound(self):
        records, manifest = self.evidence()
        self.obs['state']['left_gripper_measured_joints'] = [.043, .043]
        with self.assertRaisesRegex(ValueError, 'recorded endpoint'):
            self.locate(evidence=(records, manifest))
        self.obs['state']['left_gripper_measured_joints'] = [.044, .044]
        manifest[records[0]['image']]['size'] = [320, 240]
        with self.assertRaisesRegex(ValueError, 'resolution'):
            self.locate(evidence=(records, manifest))

    def test_missing_and_mismatched_calibration(self):
        evidence = self.evidence()
        self.obs['camera_calibration_id'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'Current'): self.locate(evidence=evidence)
        self.obs['camera_calibration_id'] = self.geometry.calibration_id
        self.observations[1].pop('camera_calibration_id'); self.save(1)
        with self.assertRaisesRegex(ValueError, 'interval'): self.locate(evidence=evidence)
        self.observations[1]['camera_calibration_id'] = self.geometry.calibration_id; self.save(1)
        self.observations[0]['camera_calibration_id'] = 'b' * 64; self.save(0)
        with self.assertRaisesRegex(ValueError, 'Historical'): self.locate()

    def test_intermediate_manipulation_cannot_be_hidden_by_endpoints(self):
        original = deepcopy(self.observations[1]['state'])
        changes = [('left_gripper_measured_joints', [.01, .01]), ('right_gripper_measured_joints', [.035, .035]),
                   ('right_ee_joint_state', [0.]), ('right_ee_pose', [.42, 0, 0, 1, 0, 0, 0])]
        for key, value in changes:
            self.observations[1]['state'] = {**deepcopy(original), key: value}; self.save(1)
            with self.subTest(key=key), self.assertRaises(ValueError): self.locate()
        self.observations[1]['state'] = original; self.save(1)
        (self.frames / 'step-0001.json').unlink()
        with self.assertRaises(FileNotFoundError): self.locate()

    def test_moving_target_inconsistent_and_unobservable_cases(self):
        request = deepcopy(self.request); request['samples'][-1]['pixel'] = self.pixel(4, self.point + [0, .025, 0])
        with self.assertRaises(ValueError): self.locate(request)
        # Motion along epipolar geometry can imitate a different static depth.
        request['samples'][-1]['pixel'] = self.pixel(4, self.point + [.02, 0, 0])
        result = self.locate(request)
        self.assertGreater(np.linalg.norm(np.array(result['position']) - self.point), .05)
        self.assertFalse(result['stationarity']['verified'])

    def test_pure_camera_rotation_and_short_baseline_rejected(self):
        mount = np.asarray(self.geometry.calibration['cameras'][self.camera]['ee_from_camera'])
        for baseline in (0., .01):
            for step in range(5):
                t = np.eye(4); t[:3, :3] = Rotation.from_euler('z', step * 2, degrees=True).as_matrix(); t[0, 3] = baseline * step / 4
                ee = t @ np.linalg.inv(mount)
                self.observations[step]['state']['left_ee_pose'] = [*ee[:3, 3], *Rotation.from_matrix(ee[:3, :3]).as_quat()[[3, 0, 1, 2]]]; self.save(step)
            request = {**self.request, 'samples': [{'step': s, 'pixel': self.pixel(s)} for s in (0, 4)]}
            with self.assertRaisesRegex(ValueError, 'parallax'): self.locate(request)

    def test_native_schema(self):
        registry = ToolRegistry({})
        args = {**META, **self.request}
        self.assertEqual(registry.decision('triangulate_temporal_point', json.dumps(args))['action'], {'triangulate_temporal': self.request})
        for change in ({'stationary_target': False}, {'camera': 'cam_head'}, {'path': '/private'}, {'step': True}, {'samples': []}):
            with self.assertRaises(ValueError): registry.decision('triangulate_temporal_point', json.dumps({**args, **change}))

    def test_real_controller_review_binding_and_zero_motion_results(self):
        skill = self.root / 'skills/fixture'; skill.mkdir(parents=True); (skill / 'SKILL.md').write_text('Use public RGB.')
        memory = self.root / 'memory.json'; memory.write_text('{"head_camera":{}}')
        credential = self.root / 'credential.py'; credential.write_text('client=AzureOpenAI(api_key="TEST_ONLY")')
        config = {'action_interface': 'tcp', 'credentials': str(credential), 'astra_provider': 'azure-astra', 'model': 'gpt-6-astra', 'reasoning_effort': 'xhigh',
            'request_timeout': 10, 'max_model_calls': 5, 'skills_root': str(skill.parent), 'skill_names': ['fixture'],
            'memory': str(memory), 'public_task_requirements': 'Fixture only'}
        config_path = self.root / 'config.json'; config_path.write_text(json.dumps(config))
        review = {**META, 'frames': [{'step': s, 'camera': self.camera} for s in (0, 4)]}
        args = {**META, **self.request}
        client = Client([response([call(name, value, i)]) for i, (name, value) in enumerate([
            ('triangulate_temporal_point', args), ('review_frames', review), ('triangulate_temporal_point', args),
            ('triangulate_temporal_point', args), ('verify_completion', META)], 1)])
        obs = {**deepcopy(self.obs), 'action_interface': 'tcp'}; commands = []
        class Robot:
            def __init__(self, episode, trace):
                self.obs = deepcopy(obs); self.home = {arm: obs['state'][arm + '_ee_pose'] for arm in ('left', 'right')}
            def command(self, op, **kwargs):
                commands.append((op, kwargs)); self.obs = {**self.obs, 'step': 10, 'ended': True}
        trace = self.root / 'trace'
        with patch.object(skill_policy, 'MultiViewGeometry', return_value=self.geometry), patch.object(skill_policy, 'Robot', Robot), \
             patch.object(native_responses, 'OpenAI', return_value=client), \
             patch.object(sys, 'argv', ['policy', '--config', str(config_path), '--episode', str(self.episode), '--trace', str(trace)]):
            skill_policy.main()
        self.assertEqual(commands, [('move', {'arms': {}, 'steps': 6, 'settle': 0, 'label': 'model_verify'})])
        result = json.loads(client.requests[3]['input'][-2]['output'])['feedback']['temporal_triangulation']
        np.testing.assert_allclose(result['position'], self.point, atol=1e-9)
        for i in (1, 4):
            rejection = json.loads(client.requests[i]['input'][-2]['output'])['feedback']
            self.assertIn('rejected_before_motion', rejection); self.assertEqual(rejection['native_steps_executed'], 0)
        self.assertEqual(json.loads((trace / 'policy-result.json').read_text())['invalid_actions'], 2)


if __name__ == '__main__': unittest.main()
