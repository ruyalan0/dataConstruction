"""Exercise the new wire messages, executed values and upstream timing without API/GPU use."""
import ast
import base64
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness')]
import policy
from full_tool_retry_policy import RETRY_POLICY
from egg_harness import native_responses
from egg_harness.control_timing import configure_timing, timing_record, verify_environment_timing
from egg_harness.io_contract import deduplicate_observation, execution_phase, round_output
from egg_harness.native_tools import ToolRegistry


def arguments():
    return dict(assessment='observed', plan='bounded motion', memory='latest memory',
                left=None, right=dict(control='joint', joints=[0.12345, -0.12345, 0.00001, 0.1, 0., 0.],
                                      gripper=0.1234567), steps=10, settle=1)


class PrecisionTests(unittest.TestCase):
    def test_schema_rejects_phase_and_keeps_memory(self):
        registry = ToolRegistry({'action_interface': 'joint'})
        schema = next(item for item in registry.definitions if item['name'] == 'control_arms')['parameters']
        self.assertNotIn('phase', schema['properties'])
        self.assertIn('memory', schema['required'])
        data = arguments()
        data['phase'] = 'transit'
        with self.assertRaises(ValueError):
            registry.decision('control_arms', json.dumps(data))

    def test_rounding_and_internal_execution_category(self):
        registry = ToolRegistry({'action_interface': 'joint'})
        result = registry.decision('control_arms', json.dumps(arguments()))
        self.assertNotIn('phase', result)
        right = result['action']['control_arms']['arms']['right']
        self.assertEqual(right['joints'][:3], [0.1235, -0.1235, 0.0])
        self.assertEqual(right['gripper'], 0.1235)
        self.assertEqual(execution_phase(result), 'transit')
        result['action']['control_arms']['steps'] = 6
        self.assertEqual(execution_phase(result), 'fine')
        self.assertEqual(round_output([True, 10, None]), [True, 10, None])

    def test_invalid_output_cannot_be_rounded_into_valid_bounds(self):
        data = arguments()
        data['right']['gripper'] = 1.00001
        with self.assertRaises(ValueError):
            ToolRegistry({'action_interface': 'joint'}).decision('control_arms', json.dumps(data))


class HistoryTests(unittest.TestCase):
    def test_unequal_pairs_are_preserved_and_input_objects_not_mutated(self):
        with tempfile.TemporaryDirectory() as directory:
            picture = Path(directory) / 'scene.jpg'
            Image.new('RGB', (10, 10), 'white').save(picture)
            prior = dict(state={'value': 1}, arm_posture={'value': 1})
            previous = [dict(role='user', content=[dict(type='input_text', text=json.dumps(prior)),
                        dict(type='input_text', text='cam_head'),
                        dict(type='input_image', image_url='data:image/jpeg;base64,' +
                             base64.b64encode(b'different bytes').decode())]),
                        dict(type='function_call', call_id='one', arguments=json.dumps(dict(memory='old'))),
                        dict(type='function_call_output', call_id='one',
                             output=json.dumps(dict(feedback={'value': 1})))]
            context = dict(previous_observation={'state': {'value': 2}},
                           previous_arm_posture={'value': 2}, memory='new', previous_result={'value': 2})
            before = deepcopy(context)
            result, images, report = deduplicate_observation(context, {'previous_head': picture}, previous)
            self.assertEqual(context, before)
            self.assertEqual(result, before)
            self.assertIn('previous_head', images)
            self.assertEqual(len(report['retained_nonidentical']), 5)
            self.assertEqual(report['removed'], [])

    def test_three_real_message_cycles_keep_one_copy_of_each_proposed_pair(self):
        payloads, model_outputs = [], []

        def create(**kwargs):
            payloads.append(deepcopy(kwargs))
            number = len(payloads)
            raw = dict(id='response' + str(number), status='completed',
                       output=[dict(type='function_call', name='control_arms', status='completed',
                                    call_id='call' + str(number), arguments=json.dumps(arguments()))])
            model_outputs.append(deepcopy(raw))
            return SimpleNamespace(id=raw['id'], usage=None, status='completed',
                                   model_dump=lambda **options: deepcopy(raw))

        provider = SimpleNamespace(base_url='https://example.invalid', api_key='unused',
                                   audit=lambda: dict(provider='mock'), headers=lambda session: {})
        config = dict(action_interface='joint', model='gpt-6-astra', reasoning_effort='high',
                      request_timeout=300, max_model_calls=10, transport_retry=RETRY_POLICY,
                      tool_ablation=dict(mode='control_only'))
        original = native_responses.NativeToolVision
        skill_module = sys.modules.get('egg_harness.skill_policy')
        old_vision = getattr(skill_module, 'Vision', None)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            images = {}
            for name in ('cam_head', 'cam_left_wrist', 'cam_right_wrist'):
                path = folder / (name + '.jpg')
                Image.new('RGB', (10, 10), 'white').save(path)
                images[name] = path
            try:
                with patch.object(native_responses, 'OpenAI', return_value=SimpleNamespace(
                        responses=SimpleNamespace(create=create))), \
                     patch.object(native_responses, 'select_provider', return_value=provider):
                    model = policy.install()(config, folder / 'model')
                    previous_state, previous_posture, feedback = None, None, None
                    for index in range(3):
                        state = {'right_ee_pose': [0., 0., 0.9123456789 + index]}
                        posture = {'elbow': 0.123456789 + index}
                        context = dict(step=index * 11, state=state, arm_posture=posture,
                                       memory='latest memory' if index else 'fresh', previous_result=feedback,
                                       previous_observation=(dict(step=(index-1)*11, active_arm=['right'],
                                                                  state=previous_state) if index else None))
                        views = dict(images)
                        if index:
                            context['previous_arm_posture'] = previous_posture
                            views.update(previous_head=images['cam_head'],
                                         previous_right_wrist=images['cam_right_wrist'])
                        result = model.ask(json.dumps(context), views, system='task skills')
                        self.assertNotIn('phase', result)
                        feedback = {'status': 'completed', 'executed_steps': 11, 'error': 0.123456789}
                        model.observe(feedback, (index+1)*11, False)
                        previous_state, previous_posture = state, posture
                    for index, payload in enumerate(payloads):
                        self.assertEqual([item['name'] for item in payload['tools']], ['control_arms'])
                        current = payload['input'][-1]
                        context = json.loads(current['content'][0]['text'])
                        self.assertEqual(context['state']['right_ee_pose'][2], 0.9123456789 + index)
                        self.assertEqual(sum(item['type'] == 'input_image' for item in current['content']), 3)
                        if index == 0:
                            self.assertEqual(len(payload['input']), 1)
                            self.assertEqual(context['memory'], 'fresh')
                            continue
                        self.assertEqual(len(payload['input']), 4)
                        prior, call, tool, _ = payload['input']
                        self.assertEqual(tool['call_id'], call['call_id'])
                        self.assertEqual(json.loads(prior['content'][0]['text'])['state'],
                                         {'right_ee_pose': [0., 0., 0.9123456789 + index - 1]})
                        self.assertNotIn('state', context['previous_observation'])
                        for name in ('previous_arm_posture', 'memory', 'previous_result'):
                            self.assertNotIn(name, context)
                        self.assertEqual(json.loads(call['arguments'])['memory'], 'latest memory')
                        self.assertEqual(json.loads(tool['output'])['feedback'], feedback)
                        self.assertEqual(sum(item['type'] == 'input_image' for item in prior['content']), 3)
                    raw = json.loads((folder / 'model/0001.response.raw.json').read_text())
                    effective = json.loads((folder / 'model/0001.response.json').read_text())
                    self.assertEqual(raw, model_outputs[0])
                    args = json.loads(effective['output'][0]['arguments'])
                    self.assertEqual(args['right']['joints'][0], 0.1235)
                    self.assertEqual(args['right']['gripper'], 0.1235)
                    audit = json.loads((folder / 'model/0002.request.json').read_text())
                    self.assertEqual(len(audit['deduplication']['removed']), 6)
                    self.assertEqual(audit['deduplication']['retained_nonidentical'], [])
                    for item in args['right']['joints'] + [args['right']['gripper']]:
                        self.assertGreaterEqual(Decimal(str(item)).as_tuple().exponent, -4)
                    self.assertTrue((folder / 'model/0003.api-input.readable.json').is_file())
            finally:
                native_responses.NativeToolVision = original
                module = sys.modules.get('egg_harness.skill_policy')
                if module is not None:
                    module.Vision = old_vision or original


class TimingTests(unittest.TestCase):
    def test_robot_ipc_accepts_verified_timing_and_rejects_original_25_hz_service(self):
        from egg_harness.client import Robot
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'commands').mkdir()
            (folder / 'responses').mkdir()
            robot = Robot.__new__(Robot)
            robot.episode, robot.trace, robot.counter, robot.session = folder, folder, 0, 'test'
            observation = dict(step=0, state={}, cameras={}, instruction='test', ended=False,
                               budget={}, action_interface='joint', control_timing=timing_record())
            (folder / 'responses/policy-test-00001.json').write_text(json.dumps(observation))
            self.assertEqual(robot.command('observe')['control_timing']['action_hz'], 250)
            observation['control_timing']['action_hz'] = 25
            (folder / 'responses/policy-test-00002.json').write_text(json.dumps(observation))
            with self.assertRaises(ValueError):
                robot.command('observe')

    def test_upstream_interpolation_uses_one_physics_cycle_instead_of_ten(self):
        cfg = SimpleNamespace(sim=SimpleNamespace(dt=0.004, decimation=1, render_interval=10),
                              eval_cfg=SimpleNamespace(observation=SimpleNamespace(collect_freq=25)),
                              camera=SimpleNamespace(default_frequency=25))
        timing = configure_timing(cfg)
        self.assertEqual(timing['action_interval_seconds'], 0.004)
        self.assertEqual(cfg.eval_cfg.observation.collect_freq, 250)
        self.assertEqual(cfg.camera.default_frequency, 250)
        self.assertEqual(cfg.sim.render_interval, 1)
        tree = ast.parse((ROOT / 'dependencies/RoboDojo/src/eval_client/eval_env.py').read_text())
        method = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == 'process_control_info')
        namespace = dict(deepcopy=deepcopy)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     '<upstream process_control_info>', 'exec'), namespace)
        env = SimpleNamespace(dt=0.004, obs_manager=SimpleNamespace(collect_freq=250, collect_interval=1.0),
                              robot_manager=SimpleNamespace(robot_list=[]))
        verify_environment_timing(env)
        self.assertEqual(len(namespace['process_control_info'](env, {'joint': 1}, 0)), 1)
        env.obs_manager.collect_freq, env.obs_manager.collect_interval = 25, 10.0
        self.assertEqual(len(namespace['process_control_info'](env, {'joint': 1}, 0)), 10)
        with self.assertRaises(ValueError):
            verify_environment_timing(env)

    def test_incompatible_physics_is_rejected(self):
        cfg = SimpleNamespace(sim=SimpleNamespace(dt=0.002, decimation=1))
        with self.assertRaises(ValueError):
            configure_timing(cfg)


class PolicyLoopTests(unittest.TestCase):
    def test_main_executes_phase_free_model_decision(self):
        from egg_harness import skill_policy
        source = ROOT / ('runs/general-pickup-control-only-20261015-155448/'
                         'general_pickup/L20/policy-20/model/0001.request.json')
        request = json.loads(source.read_text())
        initial = json.loads(request['question'])
        commands, model_questions = [], []

        class Robot:
            def __init__(self, episode, trace):
                self.episode = episode
                self.home, self.home_joints = initial['home'], initial['home_joints']
                self.obs = dict(step=0, state=initial['state'], instruction=initial['instruction'],
                                budget=initial['budget'], action_interface='joint', ended=False,
                                control_timing=timing_record(),
                                cameras={item['name']: item['path'] for item in request['images']})

            def command(self, operation, **kwargs):
                commands.append((operation, kwargs))
                self.obs = {**self.obs, 'step': kwargs['action']['steps'] + kwargs['action']['settle'],
                            'ended': True, 'action_feedback': dict(
                                status='episode_ended', executed_steps=11)}

        class Vision:
            def __init__(self, config, trace):
                self.calls = 0

            def ask(self, question, images, system=None):
                self.calls += 1
                model_questions.append(json.loads(question))
                return ToolRegistry({'action_interface': 'joint'}).decision(
                    'control_arms', json.dumps(arguments()))

            def observe(self, feedback, step, ended):
                pass

            def fail_pending(self, error, step):
                raise AssertionError('Policy loop failed') from error

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            episode = folder / 'episode'
            episode.mkdir()
            config = json.loads((HERE / 'general_pickup.json').read_text())
            config.update(memory=str(HERE / 'harness/memory/skills.json'),
                          skills_root=str(HERE / 'harness/skills'))
            path = folder / 'config.json'
            path.write_text(json.dumps(config))
            argv = ['skill_policy', '--config', str(path), '--episode', str(episode),
                    '--trace', str(folder / 'trace')]
            with patch.object(skill_policy, 'Robot', Robot), patch.object(skill_policy, 'Vision', Vision), \
                 patch.object(sys, 'argv', argv), patch('builtins.print'):
                skill_policy.main()
            decision = json.loads((folder / 'trace/decisions.jsonl').read_text())['decision']
            self.assertNotIn('phase', decision)
            self.assertEqual(commands[0][0], 'control_action')
            self.assertEqual(commands[0][1]['phase'], 'transit')
            self.assertEqual(commands[0][1]['action']['arms']['right']['joints'][0], 0.1235)
            self.assertEqual(model_questions[0]['control_timing']['action_hz'], 250)
            result = json.loads((folder / 'trace/policy-result.json').read_text())
            self.assertEqual(result['status'], 'native_end')
            self.assertEqual(result['model_calls'], 1)


if __name__ == '__main__':
    unittest.main()
