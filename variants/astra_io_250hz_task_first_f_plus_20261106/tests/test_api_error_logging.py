"""API error auditing must preserve the existing request and retry behavior."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

import httpx
from openai import InternalServerError

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE / 'harness')]
from policy import with_api_error_logging
from full_tool_retry_policy import make_retrying_create


class ApiErrorLoggingTests(unittest.TestCase):
    def error(self):
        response = httpx.Response(503, text='{"error":{"message":"backend unavailable secret-test-key"}}',
                                  headers={'x-request-id': 'req-test', 'set-cookie': 'private-cookie'},
                                  request=httpx.Request('POST', 'https://gateway.invalid/v1/responses'))
        return InternalServerError('backend unavailable secret-test-key', response=response, body=None)

    def test_full_error_saved_redacted_and_original_exception_reraised(self):
        error = self.error()
        with tempfile.TemporaryDirectory() as directory:
            create = Mock(side_effect=error)
            logged = with_api_error_logging(create, directory, lambda: 7, 'secret-test-key')
            with self.assertRaises(InternalServerError) as caught:
                logged(model='gpt-6-astra', input=[{'role': 'user', 'content': 'test'}])
            self.assertIs(caught.exception, error)
            root = Path(directory)
            latest = json.loads((root / 'latest-api-error.json').read_text())
            self.assertEqual(latest['http_status'], 503)
            self.assertEqual(latest['call'], 7)
            self.assertEqual(latest['request_id'], 'req-test')
            self.assertEqual(latest['response_body'], error.response.text.replace('secret-test-key', '[REDACTED]'))
            self.assertIn('InternalServerError', latest['traceback'])
            self.assertNotIn('secret-test-key', (root / 'api-errors.jsonl').read_text())
            self.assertNotIn('set-cookie', latest['response_headers'])
            self.assertEqual(json.loads((root / 'api-errors.jsonl').read_text()), latest)

    def test_success_and_request_are_unchanged(self):
        result = object()
        with tempfile.TemporaryDirectory() as directory:
            create = Mock(return_value=result)
            logged = with_api_error_logging(create, directory, lambda: 1, 'secret-test-key')
            kwargs = dict(model='gpt-6-astra', input=[{'role': 'user', 'content': 'test'}])
            self.assertIs(logged(**kwargs), result)
            create.assert_called_once_with(**kwargs)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_retry_recovers_with_identical_request(self):
        result = Mock(id='response-test')
        delays = []
        with tempfile.TemporaryDirectory() as directory:
            create = Mock(side_effect=[self.error(), result])
            logged = with_api_error_logging(create, directory, lambda: 1, 'secret-test-key')
            retry = make_retrying_create(logged, directory, lambda: 1, lambda: None, sleep=delays.append)
            self.assertIs(retry(model='gpt-6-astra', input='test'), result)
            self.assertEqual(create.call_args_list[0], create.call_args_list[1])
            self.assertEqual(sum(delays), 60)
            status = json.loads((Path(directory) / 'retry-status.json').read_text())
            self.assertEqual(status['state'], 'recovered')
            errors = (Path(directory) / 'api-errors.jsonl').read_text().splitlines()
            self.assertEqual(len(errors), 1)


if __name__ == '__main__':
    unittest.main()
