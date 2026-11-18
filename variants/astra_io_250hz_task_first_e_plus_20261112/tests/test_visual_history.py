"""Verify causal sampling, endpoint union and capture during native action segments."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness'), str(HERE / 'harness/scripts')]
from egg_harness.observation_pair import CAMERAS, build_visual_input, snapshot
from egg_harness.io_contract import round_numbers
from egg_harness.visual_history import (METADATA, RollingVisualHistory, expected_indices,
                                       is_sample_tick, sample_tick, validate_window)
from test_transport import context
from test_joint_goal import RateLimitedPhysics, command
from privileged_fixture import attach_scene


def pictures(step):
    return {name: f'/public/frame-{step}-{name}.jpg' for name in CAMERAS}


def window_until(step):
    sampler = RollingVisualHistory()
    for tick in range(step + 1):
        if is_sample_tick(tick):
            sampler.observe(tick, pictures(tick))
    return sampler.observe(step, pictures(step))


class WindowTests(unittest.TestCase):
    def test_grid_cannot_accumulate_62_or_63_tick_rounding_drift(self):
        self.assertEqual([sample_tick(i) for i in range(9)], [0, 63, 125, 188, 250, 313, 375, 438, 500])
        for i in range(1000):
            self.assertIn(sample_tick(i) * 4 - i * 250, (0, 2))
            self.assertTrue(is_sample_tick(sample_tick(i)))
        self.assertFalse(is_sample_tick(62))
        self.assertFalse(is_sample_tick(64))

    def test_sliding_window_startup_boundary_pruning_and_duplicate_captures(self):
        sampler = RollingVisualHistory()
        for tick in range(4001):
            if is_sample_tick(tick):
                window = sampler.observe(tick, pictures(tick))
                self.assertEqual(window, sampler.observe(tick, pictures(tick + 1)))
            else:
                window = sampler.observe(tick, pictures(tick))
            self.assertLessEqual(len(sampler.frames), 21)
            self.assertEqual([f['sample_index'] for f in window['frames']], expected_indices(tick))
            self.assertTrue(all(tick - 1250 <= f['step'] <= tick for f in window['frames']))
        self.assertEqual(len(window['frames']), 21)
        self.assertEqual(window['frames'][0]['step'], 2750)
        self.assertEqual(window['frames'][-1]['step'], 4000)

    def test_missing_sample_rewind_and_wrong_camera_are_explicit_errors(self):
        sampler = RollingVisualHistory()
        with self.assertRaisesRegex(ValueError, 'Missed'): sampler.observe(1, pictures(1))
        with self.assertRaisesRegex(ValueError, 'three'): sampler.observe(0, {'cam_head': 'a'})
        sampler.observe(0, pictures(0))
        sampler.observe(60, pictures(60))
        with self.assertRaisesRegex(ValueError, 'Missed'): sampler.observe(64, pictures(64))
        sampler.observe(63, pictures(63))
        with self.assertRaisesRegex(ValueError, 'rewind'): sampler.observe(62, pictures(62))

    def test_tampering_future_sample_metadata_and_state_leak_are_rejected(self):
        original = window_until(100)
        for mutate in (lambda w: w['frames'].pop(),
                       lambda w: w.update(clock='wall'),
                       lambda w: w['frames'][0].update(step=101),
                       lambda w: w['frames'][0].update(state={'hidden': 1}),
                       lambda w: w['frames'].reverse()):
            value = deepcopy(original); mutate(value)
            with self.assertRaises(ValueError): validate_window(value, 100)


class InputTests(unittest.TestCase):
    def build(self, step, previous=None):
        source = context(step, images=pictures(step))
        return build_visual_input(source, pictures(step), previous)

    def test_first_input_has_only_current_three_images_and_memory(self):
        payload, images, _ = self.build(0)
        self.assertEqual(len(images), 3)
        self.assertEqual(payload['visual_history']['observations'][0]['roles'], ['current'])
        self.assertEqual(payload['observation']['memory'], 'fresh memory')
        self.assertFalse(payload['history']['available'])

    def test_chronological_union_has_rgb_only_history_and_exact_endpoint_states(self):
        prior = snapshot(context(110, value=.15), pictures(110))
        payload, images, _ = self.build(1700, prior)
        entries = payload['visual_history']['observations']
        self.assertEqual(len(entries), 22)
        self.assertEqual(len(images), 66)
        self.assertEqual(entries[0]['step'], 110)  # Explicit previous decision older than 5 seconds.
        self.assertEqual(entries[0]['roles'], ['previous_decision'])
        self.assertEqual(entries[-1]['step'], 1700)
        self.assertEqual(payload['history']['duration_ms'], 6360)
        self.assertEqual(payload['history']['previous_observation']['state'], round_numbers(prior['state']))
        for entry in entries:
            self.assertLessEqual(entry['relative_time_ms'], 0)
            if entry['roles'] == ['history']:
                self.assertGreaterEqual(entry['relative_time_ms'], -5000)
                self.assertNotIn('state', entry)
                self.assertNotIn('state_source', entry)
        self.assertEqual(list(images), [name for entry in entries for name in entry['images']])
        self.assertTrue(all('memory' not in entry for entry in entries))

    def test_previous_decision_merges_with_sample_but_keeps_exact_decision_pictures(self):
        prior = snapshot(context(63), pictures('decision-63'))
        payload, images, _ = self.build(125, prior)
        entries = payload['visual_history']['observations']
        self.assertEqual([entry['step'] for entry in entries], [0, 63, 125])
        self.assertEqual(entries[1]['roles'], ['history', 'previous_decision'])
        self.assertEqual([images[name] for name in entries[1]['images']], list(pictures('decision-63').values()))

    def test_rejected_zero_motion_keeps_both_endpoints_and_zero_duration(self):
        prior = snapshot(context(83), pictures('previous-83'))
        payload, images, _ = self.build(83, prior)
        self.assertEqual(payload['history']['duration_ms'], 0)
        entries = payload['visual_history']['observations']
        self.assertEqual([entry['step'] for entry in entries], [0, 63, 83, 83])
        self.assertEqual(entries[-2]['roles'], ['previous_decision'])
        self.assertEqual(entries[-1]['roles'], ['current'])
        self.assertEqual(len(images), 12)

    def test_raw_history_is_required_and_not_synthesized_from_decisions(self):
        source = context(83); source.pop('visual_history')
        with self.assertRaisesRegex(ValueError, 'visual_history'):
            build_visual_input(source, pictures(83))


class ServerIntegrationTests(unittest.TestCase):
    def test_long_native_segment_captures_all_samples_and_exposes_only_public_rgb(self):
        import server_loop
        from egg_harness.timed_joint import TimedJointEnvironment
        from egg_harness.control_timing import timing_record
        native = RateLimitedPhysics(speed=.1, task_steps=300)
        native.success = [False]
        attach_scene(native)
        native.get_obs = lambda: dict(state=native.state())
        env = TimedJointEnvironment(native)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'control-timing.json').write_text(json.dumps(timing_record()))
            captures = []
            def capture(environment):
                tick = env.take_action_cnt[0]
                captures.append(tick)
                if len(captures) == 1:
                    for name, request in (
                        ('0001', dict(command(.8), id='0001')),
                        ('0002', dict(op='control_action', phase='transit', action=dict(arms={}))),
                        ('zz-runner-finish', dict(op='finish'))):
                        (folder / 'commands' / (name + '.json')).write_text(json.dumps(dict(request, id=name)))
                return dict(state=native.state(), instruction='Observe motion', vision={name: dict(
                    color=np.full((8, 8, 3), (tick // 10) % 255, dtype=np.uint8)) for name in CAMERAS})
            with patch.object(server_loop, 'capture_public_observation', side_effect=capture), \
                 patch.object(server_loop, 'validate_camera_rig', return_value='a' * 64), \
                 patch('egg_harness.joint_goal.check_configured_storage'):
                server_loop.run_server(env, folder, [0], 'frozen', 'general_pickup')
            response = json.loads((folder / 'responses/0001.json').read_text())
            audit = [json.loads(line) for line in (folder / 'visual-history.jsonl').read_text().splitlines()]
            self.assertEqual([row['step'] for row in audit], [sample_tick(i) for i in range(env.take_action_cnt[0]*2//125+1)])
            self.assertIn(63, captures)
            self.assertIn(125, captures)
            self.assertIn(188, captures)
            self.assertEqual(len(native.frames), env.take_action_cnt[0])
            self.assertEqual(env.goal_submissions, 2)
            self.assertGreater(native.frames[0]['left_arm_joint_state']['position'][0], 0.)
            self.assertLess(native.frames[0]['left_arm_joint_state']['position'][0], .001)
            self.assertLess(max(frame['left_arm_joint_state']['position'][0] for frame in native.frames), .8)
            self.assertEqual(native.take_action_cnt, [env.take_action_cnt[0]//10])
            self.assertEqual(native.reward_ticks, list(range(10, len(native.frames)+1, 10)))
            self.assertEqual(response['step'], 200)
            self.assertEqual(response['privileged_scene']['step'], response['step'])
            self.assertIn('target', response['privileged_scene']['objects'])
            scene_rows = [json.loads(line) for line in (folder/'privileged-scene.jsonl').read_text().splitlines()]
            self.assertTrue(scene_rows)
            self.assertLess(len(scene_rows), len(captures))
            self.assertEqual(response['action_feedback']['status'], 'observation_ready')
            self.assertEqual(response['action_feedback']['executed_steps'], response['step'])
            self.assertEqual(response['action_feedback']['goal_submissions'], 1)
            self.assertGreaterEqual(response['visual_history']['frames'][0]['step'], response['step']-1250)
            self.assertEqual(response['visual_history']['frames'][-1]['step'], sample_tick(response['step']*2//125))
            self.assertNotIn('state', response['visual_history']['frames'][0])
            for row in response['visual_history']['frames']:
                self.assertTrue(all(Path(path).is_file() for path in row['cameras'].values()))
            first = response['visual_history']['frames'][0]['cameras']['cam_head']
            last = response['visual_history']['frames'][-1]['cameras']['cam_head']
            with Image.open(first) as a, Image.open(last) as b:
                self.assertNotEqual(a.getpixel((0, 0)), b.getpixel((0, 0)))
            validate_window(response['visual_history'], response['step'])
            # Exercise the actual IPC reader's allowlist and feedback validation.
            from egg_harness.client import Robot
            robot = Robot.__new__(Robot)
            robot.episode = folder; robot.trace = folder; robot.counter = 0; robot.session = 'fixture'
            robot.obs = dict(step=0)
            (folder / 'responses/policy-fixture-00001.json').write_text(json.dumps(response))
            received = robot.command('control_action', phase='transit', action=command(.8)['action'])
            self.assertEqual(received['visual_history'], response['visual_history'])


if __name__ == '__main__':
    unittest.main()
