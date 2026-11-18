"""Qualify frozen native Responses image/tool roundtrips without moving a robot."""
import argparse
import json
from pathlib import Path
import runpy
import sys

import diagnose_suite as base
from prepare_eight_eval import save
from verify_provider_harness import verify_runtime


def preflight(harness, config_path, output, provider):
    runtime = verify_runtime(harness, require_preflight=False)
    assert runtime['zzy_supported'] and provider == 'zzy-astra'
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads(config_path.read_text())
    assert config['model'] == 'gpt-6-astra' and config['action_interface'] == 'joint'
    assert config['reasoning_effort'] == 'high'
    config['astra_provider'] = provider
    save(output / 'config.json', config)
    save(output / 'latest.json', {'step': 0})
    # verify_runtime imports only freeze, never the transport. Load it from this candidate.
    sys.path.insert(0, str(harness))
    import egg_harness
    egg_harness.__path__ = [str(harness / 'egg_harness')]
    from egg_harness import native_responses
    assert Path(native_responses.__file__).resolve().is_relative_to(harness)
    from eval_retry_policy import install
    install(native_responses, output, required_provider=provider)
    urls, wire_requests = [], []
    original = native_responses.NativeToolVision
    class TracedVision(original):
        def __init__(self, config, trace):
            super().__init__(config, trace)
            def sent(request):
                assert request.method == 'POST'
                assert str(request.url) == 'https://zzy.example.invalid/responses'
                body = json.loads(request.content)
                assert body['model'] == 'gpt-6-astra' and body['reasoning'] == {'effort': 'high'}
                assert body['store'] is False and body['parallel_tool_calls'] is False
                assert body['include'] == ['reasoning.encrypted_content']
                assert body['tool_choice'] == 'required' and body['tools'] == self.registry.definitions
                assert any(item.get('type') == 'input_image' for item in body['input'][-1]['content'])
                urls.append(str(request.url))
                wire_requests.append({'model_call': self.calls, 'model': body['model'],
                    'reasoning': body['reasoning'], 'store': body['store'],
                    'parallel_tool_calls': body['parallel_tool_calls'], 'include': body['include'],
                    'tool_result_count': sum(item.get('type') == 'function_call_output' for item in body['input'])})
            self.client._client.event_hooks['request'].append(sent)
    native_responses.NativeToolVision = TracedVision
    live = output / 'roundtrip'
    sys.argv = ['check_native_tools_live', '--output', str(live), '--config', str(output / 'config.json'),
        '--provider', provider, '--effort', config['reasoning_effort']]
    runpy.run_path(str(harness / 'scripts/check_native_tools_live.py'), run_name='__main__')
    result = json.loads((live / 'result.json').read_text())
    requests = [json.loads(p.read_text()) for p in sorted((live / 'model').glob('*.request.json'))]
    assert len(requests) == 3 and len(urls) >= 3
    assert all(r['tool_result_count'] == (0 if r['model_call'] == 1 else 1) for r in wire_requests)
    assert all(r['api'] == 'responses' and r['provider'] == provider and r['model'] == 'gpt-6-astra'
        and r['reasoning_effort'] == config['reasoning_effort'] for r in requests)
    tools = json.loads((live / 'model/native-tools.json').read_text())
    control = next(t for t in tools['tools'] if t['name'] == 'control_arms')
    assert set(control['parameters']['$defs']) == {'JointArm'}
    result.update(api='responses', model='gpt-6-astra', provider_switching=False,
        request_urls=urls, wire_requests=wire_requests, harness=runtime,
        config_sha256=base.digest(output / 'config.json'),
        public_observation_source='Synthetic RGB; no task scene and no native robot action')
    save(output / 'result.json', result)
    save(harness.with_suffix('.qualification.json'), {'status': 'passed',
        'harness_manifest_sha256': base.digest(harness / 'manifest.json'),
        'preflight_result': str(output / 'result.json'),
        'preflight_result_sha256': base.digest(output / 'result.json')})
    print(json.dumps({'status': result['status'], 'provider': provider, 'calls': 3,
        'request_urls': urls, 'native_steps': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--harness', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provider', choices=('zzy-astra',), required=True)
    args = parser.parse_args()
    preflight(args.harness.resolve(), args.config.resolve(), args.output.resolve(), args.provider)
