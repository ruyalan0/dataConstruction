"""Verify tool sets, JSON motion decoding, retries and feedback replay without API calls."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import full_tool_retry_policy as retry
import tool_ablation_policy as ablation
from test_full_tool_retry_policy import status_error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'release/harness'))
from egg_harness import native_responses
from egg_harness.skill_policy import system_prompt


class AblationTests(unittest.TestCase):
    def run_mode(self, mode):
        payloads = []
        motion = dict(left=dict(control='joint', joints=[0.0] * 6, gripper=1.0), right=None, steps=1, settle=0)
        decision = dict(assessment='test scene', plan='bounded joint motion', phase='fine', memory='retained', action=motion)
        should_fail = [True]

        def create(**kwargs):
            payloads.append(kwargs)
            if should_fail.pop() if should_fail else False:
                raise status_error(429)
            ident = 'response-' + str(len(payloads))
            if mode == 'all_tools_off':
                output = [dict(type='message', role='assistant', content=[dict(type='output_text', text=json.dumps(decision))])]
            else:
                args = {key: value for key, value in decision.items() if key != 'action'}
                args.update(motion)
                output = [dict(type='function_call', status='completed', name='control_arms',
                               call_id=ident, arguments=json.dumps(args))]
            raw = dict(id=ident, status='completed', output=output, tools=kwargs['tools'])
            return SimpleNamespace(id=ident, usage=None, status='completed', output_text=json.dumps(decision),
                                   model_dump=lambda **options: raw)

        provider = SimpleNamespace(base_url='https://example.invalid', api_key='unused',
                                   audit=lambda: dict(provider='test'), headers=lambda session: {})
        config = dict(action_interface='joint', model='gpt-6-astra', reasoning_effort='high', request_timeout=300,
                      max_model_calls=400, transport_retry=retry.RETRY_POLICY, tool_ablation=dict(mode=mode))
        original = native_responses.NativeToolVision
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            picture = path / 'scene.png'
            Image.new('RGB', (20, 20)).save(picture)
            try:
                with patch.object(native_responses, 'OpenAI', return_value=SimpleNamespace(responses=SimpleNamespace(create=create))), \
                     patch.object(native_responses, 'select_provider', return_value=provider), \
                     patch.object(retry.time, 'sleep'):
                    cls = ablation.install(mode)
                    model = cls(config, path / 'model')
                    context = json.dumps(dict(step=0, memory='fresh', joint_semantics={}, review_capability='old',
                                              multiview_geometry={}, temporal_geometry={},
                                              recent_executed_segments=[{'start_step': 0, 'end_step': 1}],
                                              stalled_segments_this_controller=[]))
                    result = model.ask(context, {'head': picture}, system_prompt('joint') + '\nFrozen task skills')
                    self.assertEqual(set(result['action']), {'control_arms'})
                    self.assertEqual(result['action']['control_arms']['arms']['left']['joints'], [0.0] * 6)
                    self.assertEqual(model.calls, 1)
                    self.assertEqual(payloads[0], payloads[1])
                    self.assertEqual({tool['name'] for tool in payloads[1]['tools']}, ablation.MODES[mode])
                    request = json.loads((model.trace / '0001.request.json').read_text())
                    self.assertFalse({'review_capability', 'multiview_geometry', 'temporal_geometry'} & json.loads(request['question']).keys())
                    self.assertIn('Frozen task skills', request['system'])
                    self.assertEqual(json.loads(request['question'])['memory'], 'fresh')
                    self.assertEqual(result['memory'], 'retained')
                    self.assertIn('recent_executed_segments', json.loads(request['question']))
                    self.assertIn('stalled_segments_this_controller', json.loads(request['question']))
                    if mode == 'control_only':
                        schema = payloads[1]['tools'][0]['parameters']
                        self.assertIn('memory', schema['properties'])
                        self.assertIn('memory', schema['required'])
                        self.assertIn('Preserve compact episode memory', request['system'])
                        args = {key: value for key, value in decision.items() if key != 'action'}
                        args.update(motion)
                        args.pop('memory')
                        with self.assertRaises(ValueError):
                            model.registry.decision('control_arms', json.dumps(args))
                    model.observe({'native_steps_executed': 1}, 1, False)
                    model.ask(json.dumps(dict(step=1, memory='same episode')), {'head': picture}, system_prompt('joint'))
                    self.assertEqual(model.calls, 2)
                    self.assertGreater(len(payloads[-1]['input']), 1)
                    if mode == 'all_tools_off':
                        self.assertEqual(payloads[-1]['tool_choice'], 'none')
                        self.assertFalse(any(item.get('type') == 'function_call_output' for item in payloads[-1]['input']))
                    else:
                        previous, function, feedback, current = payloads[-1]['input']
                        self.assertEqual(previous, payloads[1]['input'][0])
                        self.assertEqual(function['type'], 'function_call')
                        self.assertEqual(json.loads(function['arguments'])['memory'], 'retained')
                        self.assertEqual(feedback['type'], 'function_call_output')
                        self.assertEqual(feedback['call_id'], function['call_id'])
                        self.assertEqual(json.loads(feedback['output'])['feedback'], {'native_steps_executed': 1})
                        self.assertEqual(json.loads(current['content'][0]['text'])['memory'], 'same episode')
                        with self.assertRaises(ValueError):
                            model.registry.decision('review_frames', '{}')
            finally:
                native_responses.NativeToolVision = original

    def test_all_tools_off(self):
        self.run_mode('all_tools_off')

    def test_control_only(self):
        self.run_mode('control_only')


if __name__ == '__main__':
    unittest.main()
