"""CPU regressions for native tool boundaries, replay, failure and real policy routing."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openai.types.responses import Response
from PIL import Image

from egg_harness import native_responses, skill_policy
from egg_harness.native_tools import ToolRegistry
from egg_harness.storage import StoragePressure
from egg_harness.usage import model_usage


META = {'assessment': 'Public evidence only', 'plan': 'Inspect then act',
        'phase': 'transit', 'memory': 'Preserve observed result'}
TEST_PROVIDER_URLS = {name: 'https://' + name + '.example.org/v1'
                      for name in ('azure-astra', 'zzz-astra', 'zzy-astra')}


def call(name, arguments, number=1):
    return {'type': 'function_call', 'id': f'fc_test_{number}', 'call_id': f'call_test_{number}',
            'name': name, 'arguments': json.dumps(arguments), 'status': 'completed'}


def response(items, status='completed'):
    return Response.model_validate({'id': 'resp_test', 'created_at': 1, 'object': 'response',
        'status': status, 'model': 'gpt-6-astra', 'output': items,
        'parallel_tool_calls': False, 'tool_choice': 'required', 'tools': [],
        'usage': {'input_tokens': 20, 'output_tokens': 10, 'total_tokens': 30,
                  'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
                  'output_tokens_details': {'reasoning_tokens': 5}}})


class Client:
    def __init__(self, responses):
        self.queue = iter(responses)
        self.requests = []
        self.responses = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        value = next(self.queue)
        if isinstance(value, BaseException):
            raise value
        return value


class NativeToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rsi-native-check-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        credential = self.root / 'credentials.py'
        credential.write_text('raise RuntimeError("must not execute")\n'
                              'client = AzureOpenAI(api_key="TEST_ONLY", api_version="old", azure_endpoint="https://old.invalid")\n')
        self.config = {'action_interface': 'tcp', 'credentials': str(credential), 'model': 'gpt-6-astra', 'astra_provider': 'azure-astra',
                       'provider_base_urls': TEST_PROVIDER_URLS,
                       'reasoning_effort': 'xhigh', 'request_timeout': 10, 'max_model_calls': 5}

    def model(self, client, name='model'):
        with patch.object(native_responses, 'OpenAI', return_value=client) as constructor:
            model = native_responses.NativeToolVision(self.config, self.root / name)
        self.assertEqual(constructor.call_args.kwargs['max_retries'], 0)
        self.assertEqual(constructor.call_args.kwargs['base_url'], TEST_PROVIDER_URLS['azure-astra'])
        return model

    def test_schema_and_allowlist(self):
        registry = ToolRegistry({'action_interface': 'tcp'})
        self.assertEqual(set(registry.contracts), {'review_frames', 'project_pixels', 'move_arm',
                         'move_arms', 'verify_completion', 'stop_episode', 'triangulate_point', 'triangulate_temporal_point'})
        self.assertNotIn('shell', registry.contracts)
        for name in ('shell', 'execute_joint_delta', 'recover_posture', '__import__'):
            with self.assertRaises(ValueError): registry.decision(name, '{}')
        def inspect(schema):
            if isinstance(schema, dict):
                if schema.get('type') == 'object':
                    self.assertIs(schema['additionalProperties'], False)
                    self.assertEqual(set(schema['required']), set(schema['properties']))
                for value in schema.values(): inspect(value)
            elif isinstance(schema, list):
                for value in schema: inspect(value)
        full = ToolRegistry({'action_interface': 'both', 'direct_posture_action': True, 'posture_recovery_program': True, 'joint_control': True})
        for tool in full.definitions: inspect(tool['parameters'])
        valid = {**META, 'frames': [{'step': 0, 'camera': 'cam_head'}]}
        self.assertEqual(registry.decision('review_frames', json.dumps(valid))['action'], {'review': valid['frames']})
        for bad in ({**valid, 'path': '/private'}, {**valid, 'frames': [{'step': True, 'camera': 'cam_head'}]},
                    {**valid, 'frames': []}, {**valid, 'phase': 'ignored'},
                    {**valid, 'frames': [{'step': 0, 'camera': 'hidden_state'}]}):
            with self.assertRaises(ValueError): registry.decision('review_frames', json.dumps(bad))
        with self.assertRaises(ValueError):
            registry.decision('project_pixels', json.dumps({**META, 'points': [{'pixel': [1, 2], 'z': float('nan')}]}))
        joints = {f'joint{i}': None for i in range(1, 7)}
        joints['joint2'] = .1
        args = {**META, 'left': None, 'right': joints, 'steps': 2}
        preview = full.decision('preview_joint_delta', json.dumps(args))
        self.assertEqual(preview['action'], {'preview_joint_delta': {'joint_delta': {'right': {'joint2': .1}}, 'steps': 2}})
        execute = full.decision('execute_joint_delta', json.dumps({**args, 'preview_id': 'a' * 64}))
        self.assertEqual(execute['action'], {'joint_delta': {'right': {'joint2': .1}}, 'steps': 2, 'preview_id': 'a' * 64})
        for bad in ({**args, 'phase': 'fine'}, {**args, 'right': None}, {**args, 'steps': 7}):
            with self.assertRaises(ValueError): full.decision('preview_joint_delta', json.dumps(bad))
        pose = {'position': None, 'quaternion': None, 'toward': {'position': [.1, 0, 1], 'quaternion': None}, 'gripper': 0.0}
        move = registry.decision('move_arms', json.dumps({**META, 'left': pose, 'right': None, 'steps': 1, 'settle': 0}))
        self.assertEqual(move['action']['arms'], {'left': {'toward': {'position': [.1, 0, 1]}, 'gripper': 0.0}})
        unfold = {**META, 'arm': 'right', 'reason': 'descent_blocked', 'clearance_box': [[-.4, -.4, .1], [.4, .4, .9]], 'max_steps': 12}
        self.assertEqual(full.decision('preview_unfold_arm', json.dumps(unfold))['action']['preview_unfold_arm']['max_steps'], 12)
        self.assertEqual(full.decision('execute_unfold_arm', json.dumps({**unfold, 'preview_id': 'b' * 64}))['action']['preview_id'], 'b' * 64)

    def test_results_reasoning_images_and_bounded_replay(self):
        reasoning = {'type': 'reasoning', 'id': 'rs_test', 'summary': [], 'encrypted_content': 'opaque-test'}
        client = Client([response([reasoning, call('verify_completion', META, i)]) for i in range(1, 4)])
        model = self.model(client)
        picture = self.root / 'image.png'; Image.new('RGB', (8, 8)).save(picture)
        for i in range(3):
            decision = model.ask(json.dumps({'step': i * 6, 'memory': 'explicit memory'}), {'head': picture}, system='Public only')
            self.assertEqual(decision['action'], {'verify': True})
            with self.assertRaises(RuntimeError): model.ask('{"step":0}', {})
            model.observe({'hold_steps': 6}, (i + 1) * 6, i == 2)
        self.assertEqual(len(client.requests), 3)
        first, second, third = client.requests
        self.assertFalse(first['parallel_tool_calls']); self.assertFalse(first['store'])
        self.assertEqual(first['tool_choice'], 'required')
        self.assertEqual(second['extra_headers'], first['extra_headers'])
        self.assertEqual(second['input'][1], reasoning)
        self.assertEqual(second['input'][-2]['call_id'], 'call_test_1')
        self.assertEqual(json.loads(second['input'][-2]['output'])['step'], 6)
        self.assertEqual(len(third['input']), len(second['input']))
        self.assertEqual(third['input'][-2]['call_id'], 'call_test_2')
        self.assertEqual(first['input'][0]['content'][2]['type'], 'input_image')
        self.assertTrue(first['input'][0]['content'][2]['image_url'].startswith('data:image/png;base64,'))
        self.assertTrue(json.loads((model.trace / '0003.tool-result.json').read_text())['output'])
        self.assertEqual(model_usage(model.trace)['tokens'], 90)
        saved = (model.trace / '0001.request.json').read_text()
        self.assertNotIn('TEST_ONLY', saved); self.assertNotIn('data:image', saved)

    def test_fail_fast_without_action_or_retry(self):
        failures = [response([call('shell', META)]), response([]),
                    response([call('verify_completion', META), call('verify_completion', META, 2)]),
                    response([call('verify_completion', META)], status='incomplete'), RuntimeError('gateway failed')]
        for i, result in enumerate(failures):
            client = Client([result]); model = self.model(client, f'bad-{i}')
            with self.assertRaises((ValueError, RuntimeError)): model.ask('{"step":0}', {})
            self.assertIsNone(model.pending)
            self.assertEqual(len(client.requests), 1)
            self.assertTrue((model.trace / '0001.failure.json').is_file())
        model = self.model(Client([]), 'storage')
        with patch.object(native_responses, 'check_configured_storage', side_effect=StoragePressure('full')):
            with self.assertRaises(StoragePressure): model.ask('{"step":0}', {})
        self.assertEqual(model.calls, 0)
        client = Client([response([call('verify_completion', META)]), response([call('verify_completion', META)])])
        model = self.model(client, 'repeated')
        model.ask('{"step":0}', {}); model.observe({'hold_steps': 6}, 6, False)
        with self.assertRaises(ValueError): model.ask('{"step":6}', {})
        self.assertIsNone(model.pending)

    def test_policy_review_rejection_then_native_end(self):
        episode = self.root / 'episode'; frames = episode / 'frames'; frames.mkdir(parents=True)
        cameras = {}
        for name in ('cam_head', 'cam_left_wrist', 'cam_right_wrist'):
            path = frames / (name + '.jpg'); Image.new('RGB', (8, 8)).save(path); cameras[name] = str(path)
        state = {}
        for arm in ('left', 'right'):
            state.update({arm + '_ee_pose': [0, 0, 1, 1, 0, 0, 0],
                          arm + '_ee_joint_state': [1], arm + '_gripper_measured_joints': [.04, .04]})
        observation = {'action_interface': 'tcp', 'step': 0, 'ended': False, 'state': state, 'cameras': cameras,
                       'instruction': 'Test fixture', 'budget': {}}
        (frames / 'initial.json').write_text(json.dumps(observation))
        skill = self.root / 'skills' / 'fixture'; skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('Use public observations only.')
        memory = self.root / 'memory.json'; memory.write_text('{"head_camera":{}}')
        config = {**self.config, 'skills_root': str(skill.parent), 'skill_names': ['fixture'],
                  'memory': str(memory), 'public_task_requirements': 'Fixture only'}
        config_path = self.root / 'config.json'; config_path.write_text(json.dumps(config))
        movement = {**META, 'phase': 'fine', 'arm': 'left', 'position': [.1, 0, 1],
                    'quaternion': None, 'toward': None, 'gripper': None, 'steps': 1, 'settle': 0}
        client = Client([response([call('review_frames', {**META, 'frames': [{'step': 0, 'camera': 'cam_head'}]})]),
                         response([call('move_arm', movement, 2)]), response([call('verify_completion', META, 3)])])
        commands = []
        class Robot:
            def __init__(self, episode, trace):
                self.obs = deepcopy(observation)
                self.home = {arm: state[arm + '_ee_pose'] for arm in ('left', 'right')}
            def command(self, op, **kwargs):
                commands.append((op, kwargs))
                self.obs = {**self.obs, 'step': 6, 'ended': True}
        trace = self.root / 'trace'
        with patch.object(native_responses, 'OpenAI', return_value=client), patch.object(skill_policy, 'Robot', Robot), \
             patch.object(sys, 'argv', ['policy', '--config', str(config_path), '--episode', str(episode), '--trace', str(trace)]):
            skill_policy.main()
        self.assertEqual(commands, [('move', {'arms': {}, 'steps': 6, 'settle': 0, 'label': 'model_verify'})])
        second_result = json.loads(client.requests[1]['input'][-2]['output'])
        self.assertEqual(second_result['feedback']['native_steps_executed'], 0)
        second_context = json.loads(client.requests[1]['input'][-1]['content'][0]['text'])
        self.assertEqual(second_context['reviewed_observations'][0]['step'], 0)
        rejection = json.loads(client.requests[2]['input'][-2]['output'])
        self.assertIn('rejected_before_motion', rejection['feedback'])
        last = json.loads((trace / 'model/0003.tool-result.json').read_text())
        self.assertTrue(json.loads(last['output'])['ended'])
        result = json.loads((trace / 'policy-result.json').read_text())
        self.assertEqual(result['status'], 'native_end'); self.assertEqual(result['invalid_actions'], 1)


if __name__ == '__main__': unittest.main()
