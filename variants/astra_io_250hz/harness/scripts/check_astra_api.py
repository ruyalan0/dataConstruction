"""CPU checks for cross-process allocation and isolated native tool conversations."""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from egg_harness import astra_api, native_responses
from scripts.check_native_tools import Client, META, TEST_PROVIDER_URLS, call, response


def allocate_many(config):
    return [astra_api.select_provider(config).audit() for _ in range(10)]


class AstraAPITests(unittest.TestCase):
    def setUp(self):
        # Exercise locks on the actual shared project filesystem, not only /tmp.
        artifacts = Path(__file__).resolve().parents[1] / 'artifacts'
        artifacts.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='astra-pool-check-', dir=artifacts)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        azure = self.root / 'azure.py'
        azure.write_text('raise RuntimeError("must not execute")\n'
                         'client = AzureOpenAI(api_key="AZURE_TEST_KEY")\n')
        zzz = self.root / 'zzz.md'
        zzz.write_text('sk-ZZZ_TEST_KEY\n\nDocs: https://doc.zhizengzeng.com/doc-8449299\n')
        zzy = self.root / 'zzy.md'
        zzy.write_text('sk-ZZY_TEST_KEY\nmodel = "gpt-5.5"\nbase_url = "https://ignored.invalid"\n')
        self.sequence = self.root / 'sequence'
        self.config = {'credentials': str(azure), 'zzz_credentials': str(zzz), 'zzy_credentials': str(zzy),
                       'provider_base_urls': TEST_PROVIDER_URLS,
                       'astra_sequence_file': str(self.sequence), 'model': 'gpt-6-astra',
                       'reasoning_effort': 'xhigh', 'request_timeout': 10, 'max_model_calls': 3}

    def test_cross_process_round_robin(self):
        with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('spawn')) as pool:
            batches = list(pool.map(allocate_many, [self.config] * 4))
        rows = sorted([r for batch in batches for r in batch], key=lambda r: r['provider_allocation'])
        self.assertEqual([r['provider_allocation'] for r in rows], list(range(40)))
        self.assertEqual([r['provider'] for r in rows], list(astra_api.AUTO_PROVIDERS) * 20)
        self.assertEqual(self.sequence.read_text(), '40\n')
        # A terminated allocator leaves no capacity lease to leak or ceiling to hit.
        self.assertEqual(astra_api.select_provider(self.config).allocation, 40)

    def test_explicit_provider_does_not_use_pool(self):
        for name in astra_api.AUTO_PROVIDERS:
            config = {**self.config, 'astra_provider': name}
            config['zzz_credentials' if name == 'azure-astra' else 'credentials'] = '/missing-unused'
            with patch.object(astra_api, 'next_allocation', side_effect=AssertionError('must not allocate')):
                selected = astra_api.select_provider(config)
            self.assertEqual(selected.name, name)
            self.assertIsNone(selected.allocation)
            self.assertNotIn('TEST_KEY', repr(selected))
        self.assertFalse(self.sequence.exists())

    def test_bad_configuration_fails_before_allocation(self):
        for value in ('misspelled', '', None, 4):
            with self.assertRaises(ValueError):
                astra_api.select_provider({**self.config, 'astra_provider': value})
        with self.assertRaises(FileNotFoundError):
            astra_api.select_provider({**self.config, 'zzz_credentials': '/missing'})
        key_file = Path(self.config['zzz_credentials'])
        for contents in ('no key', 'sk-one\nsk-two\n', 'Authorization: Bearer sk-no-standalone-key'):
            key_file.write_text(contents)
            with self.assertRaises(ValueError): astra_api.select_provider(self.config)
        self.assertFalse(self.sequence.exists())
        key_file.write_text('sk-TEST\n')
        self.sequence.write_text('corrupted\n')
        with self.assertRaises(ValueError): astra_api.select_provider(self.config)
        self.assertEqual(self.sequence.read_text(), 'corrupted\n')

    def test_sticky_provider_and_private_tool_history(self):
        clients = {}
        for name in astra_api.PROVIDERS:
            reasoning = {'type': 'reasoning', 'id': 'rs_test', 'summary': [], 'encrypted_content': name + '-opaque'}
            clients[TEST_PROVIDER_URLS[name]] = Client([
                response([deepcopy(reasoning), call('verify_completion', META, i)]) for i in range(1, 4)])
        def client(**kwargs):
            self.assertEqual(kwargs['max_retries'], 0)
            return clients[kwargs['base_url']]
        with patch.object(native_responses, 'OpenAI', side_effect=client) as constructor:
            models = [native_responses.NativeToolVision(self.config, self.root / name) for name in astra_api.AUTO_PROVIDERS]
            for step in range(3):
                for model in models:
                    model.ask(json.dumps({'step': step}), {})
                    model.observe({'echo': model.provider.name}, step + 1, step == 2)
        self.assertEqual(constructor.call_count, 2)
        self.assertEqual([c.kwargs['api_key'] for c in constructor.call_args_list], ['AZURE_TEST_KEY', 'sk-ZZZ_TEST_KEY'])
        self.assertEqual(self.sequence.read_text(), '2\n')
        for model in models:
            requests = clients[model.provider.base_url].requests
            for request in requests:
                self.assertFalse(request['parallel_tool_calls'])
                self.assertEqual(request['tools'], models[0].registry.definitions)
                self.assertEqual(request['extra_headers'], model.provider.headers(model.session_id))
            for request in requests[1:]:
                self.assertEqual(request['input'][1]['encrypted_content'], model.provider.name + '-opaque')
                self.assertEqual(json.loads(request['input'][-2]['output'])['feedback']['echo'], model.provider.name)
            for path in model.trace.glob('*.json'):
                self.assertNotIn('TEST_KEY', path.read_text())
            for suffix in ('request', 'cost'):
                record = json.loads((model.trace / ('0001.' + suffix + '.json')).read_text())
                self.assertEqual(record['provider'], model.provider.name)

    def test_gateway_failure_never_switches_or_retries(self):
        client = Client([RuntimeError('test gateway failure')])
        with patch.object(native_responses, 'OpenAI', return_value=client) as constructor:
            model = native_responses.NativeToolVision(self.config, self.root / 'failed')
            with self.assertRaisesRegex(RuntimeError, 'test gateway failure'):
                model.ask('{"step":0}', {})
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(self.sequence.read_text(), '1\n')
        self.assertIsNone(model.pending)
        self.assertEqual(json.loads((model.trace / '0001.failure.json').read_text())['provider'], 'azure-astra')

    def test_zzy_explicit_native_tool_roundtrip(self):
        client = Client([response([call('verify_completion', META, 1)]),
                         response([call('verify_completion', META, 2)])])
        config = {**self.config, 'astra_provider': 'zzy-astra', 'credentials': '/missing-azure',
                  'zzz_credentials': '/missing-zzz', 'action_interface': 'joint'}
        with patch.object(native_responses, 'OpenAI', return_value=client) as constructor:
            model = native_responses.NativeToolVision(config, self.root / 'zzy-native')
            model.ask('{"step":0}', {})
            model.observe({'marker': 'zzy-result'}, 0, False)
            model.ask('{"step":0}', {})
        self.assertEqual(constructor.call_args.kwargs['base_url'], TEST_PROVIDER_URLS['zzy-astra'])
        self.assertEqual(constructor.call_args.kwargs['api_key'], 'sk-ZZY_TEST_KEY')
        self.assertEqual(constructor.call_args.kwargs['max_retries'], 0)
        self.assertFalse(self.sequence.exists())
        for request in client.requests:
            self.assertEqual(request['model'], 'gpt-6-astra')
            self.assertEqual(request['reasoning'], {'effort': 'xhigh'})
            self.assertEqual(request['extra_headers'], {})
            self.assertFalse(request['store'])
            self.assertFalse(request['parallel_tool_calls'])
            self.assertEqual(request['include'], ['reasoning.encrypted_content'])
        self.assertIn('zzy-result', client.requests[1]['input'][-2]['output'])
        self.assertEqual(model.provider.name, 'zzy-astra')
        for path in model.trace.glob('*.json'):
            self.assertNotIn('sk-ZZY_TEST_KEY', path.read_text())

    def test_zzy_rejects_ambiguous_credentials_without_reading_others(self):
        path = Path(self.config['zzy_credentials'])
        for content in ('no key', 'sk-one\nsk-two\n', 'Authorization: Bearer sk-inline'):
            path.write_text(content)
            with self.assertRaisesRegex(ValueError, 'exactly one standalone API key'):
                astra_api.select_provider({**self.config, 'astra_provider': 'zzy-astra'})
        self.assertFalse(self.sequence.exists())

    def test_missing_endpoint_fails_before_credentials_or_allocation(self):
        config = {**self.config, 'provider_base_urls': {}}
        with patch.object(astra_api, 'credential') as credentials, patch.object(astra_api, 'next_allocation') as allocate:
            with self.assertRaises(KeyError):
                astra_api.select_provider(config)
            credentials.assert_not_called()
            allocate.assert_not_called()

    def test_example_endpoint_is_rejected(self):
        config = {**self.config, 'astra_provider': 'zzy-astra',
                  'provider_base_urls': {'zzy-astra': 'https://gateway.example.invalid/v1'}}
        with self.assertRaises(ValueError):
            astra_api.select_provider(config)


if __name__ == '__main__': unittest.main()
