"""Four-decimal model I/O must preserve source physics and effective actions."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness')]
from egg_harness.io_contract import dumps_four_decimals, round_numbers, precision_metadata
from egg_harness.observation_pair import build_pair, CAMERAS
import test_transport
from test_transport import arguments, context


class PrecisionTests(unittest.TestCase):
    def test_decimal_ties_tiny_values_and_negative_zero_use_one_rounding_rule(self):
        source = [1.23445, -1.23445, .00005, -.00005, .000049, -.000049, -0.0]
        self.assertEqual(round_numbers(source), [1.2345, -1.2345, .0001, -.0001, 0., 0., 0.])
        self.assertEqual(dumps_four_decimals(source), '[1.2345,-1.2345,0.0001,-0.0001,0.0000,0.0000,0.0000]')

    def test_fixed_width_numbers_remain_json_numbers_and_text_is_escaped_unchanged(self):
        source = dict(angle=.4, zero=0., count=20, enabled=True, missing=None,
                      memory='坐标 0.123456; "quoted"\nline', nested=[-2., 1e-6])
        text = dumps_four_decimals(source)
        self.assertIn('"angle":0.4000', text)
        self.assertIn('"zero":0.0000', text)
        self.assertIn('"count":20,', text)
        decoded = json.loads(text)
        self.assertEqual(decoded, round_numbers(source))
        self.assertIs(type(decoded['count']), int)
        self.assertIs(type(decoded['angle']), float)
        self.assertEqual(decoded['memory'], source['memory'])
        self.assertEqual(dumps_four_decimals(decoded), text)

    def test_nonfinite_nested_values_cannot_cross_either_model_boundary(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            for operation in (round_numbers, dumps_four_decimals):
                with self.assertRaisesRegex(ValueError, 'Nonfinite'):
                    operation(dict(nested=[value]))

    def test_whole_current_and_previous_payloads_round_after_full_precision_validation(self):
        images = {name: name for name in CAMERAS}
        previous_context = context(value=.123456789012)
        _, _, previous = build_pair(previous_context, images)
        source = context(83, value=-.234567890123)
        source.update(home={'left':[.123456789]}, contact_estimates={'right':[.23456789]},
                      last_motion=dict(status='motion_stalled', final_joint_error_rad={'left':.123456789},
                                       gripper_error_m={'left':.00005678}))
        source['arm_posture']['test_value'] = .123456789
        original = deepcopy(source)
        payload, _, internal = build_pair(source, images, previous)
        self.assertEqual(source, original)
        self.assertEqual(internal['state'], source['state'])
        self.assertEqual(previous['state'], previous_context['state'])
        self.assertEqual(payload, round_numbers(payload))
        self.assertEqual(payload['observation']['state']['left_arm_joint_state'][0], -.2346)
        self.assertEqual(payload['history']['previous_observation']['state']['left_arm_joint_state'][0], .1235)
        self.assertEqual(payload['observation']['last_motion']['gripper_error_m']['left'], .0001)
        # A stale FK measurement that happens to round to the same displayed
        # angle must still be rejected before presentation rounding.
        source['joint_semantics']['arms']['left'][0]['angle_rad'] += .00000001
        with self.assertRaisesRegex(ValueError, 'stale'):
            build_pair(source, images)


class PrecisionTransportTests(unittest.TestCase):
    setUp = test_transport.TransportTests.setUp
    tearDown = test_transport.TransportTests.tearDown

    def test_actual_wire_and_effective_response_use_identical_four_decimal_numbers(self):
        def create(**kwargs):
            self.payloads.append(deepcopy(kwargs))
            output = arguments(1)
            output['right'].update(joints=[0, 1, .123456, -.234567, 0, 0], gripper=1)
            raw = dict(status='completed', output=[dict(type='function_call', status='completed',
                       name='control_arms', call_id='precision', arguments=json.dumps(output))])
            return SimpleNamespace(status='completed', usage=None, model_dump=lambda **options:deepcopy(raw))
        self.model.client.responses.create = create
        source = context(value=.123456789)
        decision = self.model.ask(json.dumps(source), self.images)
        question = self.payloads[-1]['input'][0]['content'][0]['text']
        self.assertEqual(question, dumps_four_decimals(json.loads(question)))
        self.assertIn('0.1235', question)
        self.assertNotIn('0.123456789', question)
        effective = json.loads((self.folder/'model/0001.response.json').read_text())['output'][0]['arguments']
        self.assertIn('"joints":[0.0000,1.0000,0.1235,-0.2346,0.0000,0.0000]', effective)
        self.assertIn('"gripper":1.0000', effective)
        self.assertEqual(decision['action']['control_arms']['arms']['right']['joints'],
                         json.loads(effective)['right']['joints'])
        request = json.loads((self.folder/'model/0001.request.json').read_text())
        self.assertTrue(all(request[key]==value for key,value in precision_metadata().items()))
        self.assertEqual(source['state']['left_arm_joint_state'][0], .123456789)


if __name__ == '__main__':
    unittest.main()
