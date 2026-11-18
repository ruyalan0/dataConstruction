"""Exercise transient recovery through the real native decision/history pipeline."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError
from PIL import Image

import full_tool_retry_policy as retry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'release/harness'))
from egg_harness import native_responses


def status_error(code, retry_after=None):
    response = httpx.Response(code, request=httpx.Request('POST', 'https://example.invalid/v1/responses'),
                              headers={'retry-after': retry_after} if retry_after else {})
    return APIStatusError('simulated gateway error', response=response, body={})


class RecoveryTests(unittest.TestCase):
    def test_recovery_preserves_one_decision_and_prior_tool_cycle(self):
        original = native_responses.NativeToolVision
        config = dict(action_interface='joint', reasoning_effort='high', model='gpt-6-astra',
                      request_timeout=300, max_model_calls=400, transport_retry=retry.RETRY_POLICY)
        errors = [APITimeoutError(request=httpx.Request('POST', 'https://example.invalid')),
                  status_error(502), status_error(429, '310'), status_error(499)]
        payloads, waits = [], []

        def create(**kwargs):
            payloads.append(kwargs)
            if errors:
                raise errors.pop(0)
            call_id = f'call-{len(payloads)}'
            output = [dict(type='function_call', status='completed', call_id=call_id,
                           name='verify_completion', arguments=json.dumps(dict(
                               assessment='test', plan='hold and inspect', phase='fine', memory='unchanged scene')))]
            raw = dict(id=call_id, status='completed', output=output, tools=kwargs['tools'])
            return SimpleNamespace(id=call_id, status='completed', usage=None,
                                   model_dump=lambda **options: raw)

        fake_client = SimpleNamespace(responses=SimpleNamespace(create=create))
        provider = SimpleNamespace(base_url='https://example.invalid', api_key='unused',
                                   audit=lambda: dict(provider='test'), headers=lambda session: {})
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            image = folder / 'scene.png'
            Image.new('RGB', (20, 20)).save(image)
            try:
                with patch.object(native_responses, 'OpenAI', return_value=fake_client), \
                     patch.object(native_responses, 'select_provider', return_value=provider), \
                     patch.object(retry.time, 'sleep', side_effect=waits.append):
                    retry.install()
                    vision = native_responses.NativeToolVision(config, folder / 'model')
                    question = json.dumps(dict(step=7, memory='carry retained', action_interface='joint'))
                    decision = vision.ask(question, {'head': image}, system='unchanged skills')
                    self.assertEqual(vision.calls, 1)
                    self.assertEqual(len(list(vision.trace.glob('*.request.json'))), 1)
                    self.assertEqual(len(list(vision.trace.glob('*.response.json'))), 1)
                    self.assertFalse(list(vision.trace.glob('*.failure.json')))
                    self.assertEqual(len(vision.seen_call_ids), 1)
                    self.assertTrue(decision['action']['verify'])
                    self.assertTrue(all(payload == payloads[0] for payload in payloads))
                    self.assertEqual(sum(waits), 60 + 120 + 310 + 300)
                    self.assertLessEqual(max(waits), 30)
                    events = [json.loads(line) for line in (vision.trace / 'api-retries.jsonl').read_text().splitlines()]
                    self.assertEqual([event['event'] for event in events], ['retry', 'retry', 'retry', 'retry', 'recovered'])
                    self.assertEqual(events[-2]['http_status'], 499)
                    self.assertEqual(len({event['request_sha256'] for event in events}), 1)
                    vision.observe({'native_steps_executed': 6}, 13, False)
                    vision.ask(json.dumps(dict(step=13, memory='same episode')), {'head': image})
                    self.assertEqual(vision.calls, 2)
                    self.assertEqual(payloads[-1]['input'][2]['type'], 'function_call_output')
            finally:
                native_responses.NativeToolVision = original

    def test_bad_request_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            def create(**kwargs):
                raise status_error(400)
            with patch.object(retry.time, 'sleep') as sleep:
                call = retry.make_retrying_create(create, directory, lambda: 1, lambda: None)
                with self.assertRaises(APIStatusError):
                    call(input=['unchanged'])
                sleep.assert_not_called()
                self.assertFalse((Path(directory) / 'api-retries.jsonl').exists())

    def test_transport_and_retry_after_classification(self):
        self.assertTrue(retry.retryable(APIConnectionError(request=httpx.Request('POST', 'https://example.invalid'))))
        for status in (408, 409, 429, 499, 500, 502, 503, 504):
            self.assertTrue(retry.retryable(status_error(status)))
        for status in (400, 401, 403, 404, 422):
            self.assertFalse(retry.retryable(status_error(status)))
        self.assertEqual(retry.delay_for(status_error(502), 1000), 300)
        self.assertEqual(retry.delay_for(status_error(429, '360'), 1), 360)


if __name__ == '__main__':
    unittest.main()
