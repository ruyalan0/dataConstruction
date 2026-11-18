"""Regression checks for original horizons, rejected prefixes and exact labels."""

import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

import export_astra_original_ms_swift as exporter


class OriginalDecisionExportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source = self.root / 'dependencies/RoboDojo'
        for relative, content in {
            'env_cfg/sim/sim_config.yml': 'dt: 0.004\ndecimation: 1\n',
            'env_cfg/arx_x5.yml': 'observation:\n  collect_freq: 25\n',
        }.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.batch = self.make_batch('control-only', [('task', 20, True), ('failed_task', 20, False)])
        self.output = self.root / 'dataset'

    def make_batch(self, name, episodes, memory_enabled=True):
        batch = self.root / 'runs' / name
        batch.mkdir(parents=True)
        results = []
        for task, layout, success in episodes:
            run = batch / task / f'L{layout}'
            episode, policy = run / f'seed-{layout}', run / f'policy-{layout}'
            frames, model = episode / 'frames', policy / 'model'
            frames.mkdir(parents=True)
            model.mkdir(parents=True)
            specs = [(12, 1, 13, 'transit', 'completed'),
                     (3, 1, 1, 'fine', 'tracking_or_gripper_change'),
                     (2, 0, 1, 'fine', 'episode_ended')] if success else [
                         (7, 0, 0, 'fine', 'rejected_before_motion'),
                         (16, 2, 2, 'transit', 'episode_ended')]
            total = sum(spec[2] for spec in specs)
            observations = []
            for step in range(total + 1):
                state, cameras = {}, {}
                for arm in exporter.ARMS:
                    state[arm + '_arm_joint_state'] = [step * .01] * 6
                    state[arm + '_ee_joint_state'] = [.8]
                for camera, _ in exporter.CAMERAS:
                    image = frames / f'{step}-{camera}.jpg'
                    Image.new('RGB', (640, 480), (step * 7, 10, 30)).save(image)
                    cameras[camera] = str(image)
                obs = dict(step=step, state=state, cameras=cameras, action_interface='joint',
                           instruction='Place the object on the pad.', ended=step == total)
                tag = 'initial' if step == 0 else f'step-{step:04d}'
                exporter.save(frames / (tag + '.json'), obs)
                observations.append(obs)
            decisions, commands, actions = [], [dict(command=dict(op='observe', id='observe'), step=0)], []
            start, previous = 0, None
            for position, (steps, settle, executed, phase, status) in enumerate(specs, 1):
                args = dict(assessment='Visible and clear.', left=dict(control='joint',
                            joints=[.75 + position / 100] * 6 if position != 2 else None,
                            gripper=.65 if position != 2 else None), memory='Retain this episode.',
                            phase=phase, plan='Move, then inspect.', right=None, settle=settle, steps=steps)
                if not memory_enabled:
                    args.pop('memory')
                argument_text = json.dumps(args, indent=1)
                arms = {'left': {key: value for key, value in args['left'].items() if value is not None}}
                control = dict(arms=arms, steps=steps, settle=settle)
                decision = {key: args[key] for key in ('assessment', 'plan', 'phase')}
                decision['memory'] = args.get('memory', '')
                decision['action'] = dict(control_arms=control)
                decisions.append(dict(step=start, decision=decision))
                obs = observations[start]
                context = dict(step=start, state=obs['state'], instruction=obs['instruction'],
                               previous_observation=dict(step=previous) if previous is not None else None,
                               memory='Original input memory.')
                if not memory_enabled:
                    context.pop('memory')
                images = [dict(name=camera, path=obs['cameras'][camera], size=[640, 480],
                               sha256=exporter.sha256(obs['cameras'][camera])) for camera, _ in exporter.CAMERAS]
                if previous is not None:
                    path = observations[previous]['cameras']['cam_head']
                    images.append(dict(name='previous_head', path=path, size=[640, 480], sha256=exporter.sha256(path)))
                request = dict(step=start, question=json.dumps(context), images=images)
                call = dict(type='function_call', name='control_arms', call_id=f'call-{position}',
                            status='completed', arguments=argument_text)
                stem = model / f'{position:04d}'
                exporter.save(stem.with_suffix('.request.json'), request)
                exporter.save(stem.with_suffix('.response.json'), dict(status='completed', output=[call]))
                actual = dict(step=start + executed, ended=start + executed == total,
                              feedback=dict(status=status, executed_steps=executed))
                exporter.save(stem.with_suffix('.tool-result.json'), dict(type='function_call_output',
                              call_id=call['call_id'], output=json.dumps(actual)))
                command_id = f'command-{position}'
                commands.append(dict(command=dict(id=command_id, op='control_action', action=control, phase=phase),
                                     step=start + executed))
                for step in range(start + 1, start + executed + 1):
                    action = {}
                    for arm in exporter.ARMS:
                        action[arm + '_arm_joint_state'] = [step * .025] * 6
                        action[arm + '_ee_joint_state'] = [.4]
                    actions.append(dict(step=step, command=command_id, action=action))
                previous, start = start, start + executed
            for path, data in [(policy / 'decisions.jsonl', decisions), (policy / 'commands.jsonl', commands),
                               (episode / 'actions.jsonl', actions)]:
                path.write_text(''.join(exporter.compact(row) + '\n' for row in data))
            results.append(dict(task=task, layout=layout, output=str(run), success=success,
                                steps=total, native_score=100 if success else 0,
                                outcome='native_success' if success else 'native_task_failure'))
        exporter.save(batch / 'batch.json', dict(status='complete', mode='control_only',
                      enabled_tools=['control_arms'], episodes=results,
                      explicit_episode_memory=memory_enabled, model_history_replay=memory_enabled,
                      rgb_memory_enabled=False))
        exporter.save(batch / 'summary.json', dict(final_audit_passed=True))
        exporter.save(batch / 'final-audit.json', dict(passed=True))
        return batch

    def test_original_arguments_and_variable_steps_survive_interruption_and_rejection(self):
        manifest = exporter.export(self.batch, self.output)
        audit = exporter.validate(self.output)
        samples = list(exporter.rows(self.output / 'all.jsonl'))
        labels = [json.loads(s['messages'][2]['content']) for s in samples]
        indices = list(exporter.rows(self.output / 'index.jsonl'))
        self.assertEqual(manifest['counts']['all'], 5)
        self.assertEqual(manifest['counts']['train'], 3)
        self.assertEqual([a['steps'] for a in labels], [12, 3, 2, 7, 16])
        self.assertEqual(labels[0]['left']['joints'], [.76] * 6)
        self.assertEqual(labels[0]['left']['gripper'], .65)
        self.assertIsNone(labels[0]['right'])
        self.assertIsNone(labels[1]['left']['joints'])
        self.assertIsNone(labels[1]['left']['gripper'])
        source = exporter.read(Path(indices[0]['source_response']))['output'][0]['arguments']
        self.assertEqual(samples[0]['messages'][2]['content'], source)
        self.assertIn('\n ', source)  # Original whitespace is also retained.
        self.assertEqual(indices[1]['executed_native_steps'], 1)
        self.assertEqual(indices[1]['requested_steps'], 3)
        self.assertEqual(indices[1]['requested_settle'], 1)
        self.assertEqual(indices[3]['executed_native_steps'], 0)
        self.assertEqual(indices[3]['observation_step'], indices[4]['observation_step'])
        self.assertEqual(audit['zero_motion_decisions'], 1)
        self.assertEqual(audit['shorter_than_requested'], 4)
        self.assertEqual(len(samples[0]['images']), 3)
        self.assertEqual(len(samples[1]['images']), 4)
        self.assertEqual(len(samples[3]['images']), 3)
        current = json.loads(samples[1]['messages'][1]['content'].split('Observation context:\n')[1].split('\nNamed views:')[0])
        self.assertEqual(current['step'], 13)  # Decision boundary, not the adjacent native step.

    def test_audit_rejects_modified_horizon_even_if_partition_is_changed(self):
        exporter.export(self.batch, self.output)
        for filename in ('all.jsonl', 'train.jsonl'):
            samples = list(exporter.rows(self.output / filename))
            args = json.loads(samples[0]['messages'][2]['content'])
            args['steps'] = 10
            samples[0]['messages'][2]['content'] = exporter.compact(args)
            (self.output / filename).write_text(''.join(exporter.compact(s) + '\n' for s in samples))
        with self.assertRaisesRegex(ValueError, 'Original label/input mismatch'):
            exporter.validate(self.output)

    def test_audit_rejects_future_observation_image(self):
        exporter.export(self.batch, self.output)
        samples = list(exporter.rows(self.output / 'all.jsonl'))
        target = Path(samples[0]['images'][0])
        target.write_bytes(Path(samples[1]['images'][0]).read_bytes())
        with self.assertRaisesRegex(ValueError, 'Original image bytes differ'):
            exporter.validate(self.output)

    def test_merge_preserves_original_decisions_and_resets_history(self):
        exporter.export(self.batch, self.output)
        exporter.validate(self.output)
        additional = self.make_batch('additional', [('task', 21, True)])
        output = self.root / 'merged'
        manifest = exporter.merge_dataset(self.output, [additional], output)
        self.assertTrue(exporter.validate(output)['passed'])
        self.assertEqual(manifest['counts']['all'], 8)
        sample = list(exporter.rows(output / 'all.jsonl'))[5]
        self.assertEqual(len(sample['images']), 3)
        self.assertIn('episode000021', sample['images'][0])
        self.assertEqual(exporter.read(output / 'merge-record.json')['added_samples'], 3)

    def test_rejects_changed_source_and_existing_output(self):
        exporter.export(self.batch, self.output)
        with self.assertRaises(FileExistsError):
            exporter.export(self.batch, self.output)
        path = self.batch / 'task/L20/policy-20/model/0001.response.json'
        path.write_text(path.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            exporter.validate(self.output)

    def test_rejects_duplicate_or_non_control_only_source(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate task/layout'):
            exporter.export(self.batch, self.output, [self.batch])
        manifest = exporter.read(self.batch / 'batch.json')
        manifest['enabled_tools'].append('review_frames')
        exporter.save(self.batch / 'batch.json', manifest)
        with self.assertRaisesRegex(ValueError, 'control_arms-only'):
            exporter.export(self.batch, self.output)

    def test_memory_disabled_export_preserves_original_labels_without_adding_memory(self):
        batch = self.make_batch('no-memory', [('task', 21, True)], memory_enabled=False)
        manifest = exporter.export(batch, self.output)
        audit = exporter.validate(self.output)
        self.assertTrue(audit['passed'])
        self.assertEqual(manifest['memory_policy'], dict(explicit_episode_memory=False,
                         model_history_replay=False, rgb_memory_enabled=False))
        self.assertEqual(manifest['system_prompt'], exporter.NO_MEMORY_SYSTEM)
        for sample, index in zip(exporter.rows(self.output / 'all.jsonl'), exporter.rows(self.output / 'index.jsonl')):
            label = sample['messages'][2]['content']
            self.assertNotIn('memory', json.loads(label))
            original = exporter.read(Path(index['source_response']))['output'][0]['arguments']
            self.assertEqual(label, original)
            context = json.loads(sample['messages'][1]['content'].split('Observation context:\n')[1].split('\nNamed views:')[0])
            self.assertNotIn('memory', context)

    def test_rejects_mixing_memory_profiles_or_relabeling_legacy_sources(self):
        batch = self.make_batch('no-memory', [('task', 21, True)], memory_enabled=False)
        with self.assertRaisesRegex(ValueError, 'Cannot mix memory-enabled'):
            exporter.export(self.batch, self.output, [batch])
        manifest = exporter.read(self.batch / 'batch.json')
        manifest.update(explicit_episode_memory=False, model_history_replay=False)
        exporter.save(self.batch / 'batch.json', manifest)
        with self.assertRaisesRegex(ValueError, 'Response differs from source memory policy'):
            exporter.export(self.batch, self.output)

    def test_rejects_history_replay_in_memory_disabled_source(self):
        batch = self.make_batch('no-memory', [('task', 21, True)], memory_enabled=False)
        path = batch / 'task/L21/policy-21/model/0001.request.json'
        request = exporter.read(path)
        request['replayed_call_id'] = 'old-model-response'
        exporter.save(path, request)
        with self.assertRaisesRegex(ValueError, 'history ledger or model replay'):
            exporter.export(batch, self.output)


if __name__ == '__main__':
    unittest.main()
