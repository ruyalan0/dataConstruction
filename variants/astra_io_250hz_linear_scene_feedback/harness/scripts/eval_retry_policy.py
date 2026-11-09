"""Run an unchanged frozen policy with audited, identical transport retries only."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys
import time
from contextlib import nullcontext
from provider_concurrency import AZURE_LIMIT, AZURE_LOCKS, azure_request_slot


def install(transport, episode, delays=(10, 30, 60), sleep=time.sleep, required_provider=None):
    from openai import APIConnectionError, APIStatusError
    from egg_harness.vision import failure_record
    original = transport.NativeToolVision

    class RetryingVision(original):
        def __init__(self, config, trace):
            super().__init__(config, trace)
            if required_provider is not None:
                assert self.provider.name == required_provider, 'Unexpected actual API provider'
            create = self.client.responses.create
            audit = Path(trace) / 'transport-retries.jsonl'
            limited = self.provider.audit()['provider'] == 'azure-astra'

            def request(**kwargs):
                encoded = json.dumps(kwargs, sort_keys=True, allow_nan=False).encode()
                fingerprint = hashlib.sha256(encoded).hexdigest()
                scene = (episode/'latest.json').read_bytes()
                step = json.loads(scene)['step']
                for attempt in range(len(delays)+1):
                    if (episode/'latest.json').read_bytes() != scene:
                        raise RuntimeError('Scene changed before identical-request retry')
                    if hashlib.sha256(json.dumps(kwargs, sort_keys=True, allow_nan=False).encode()).hexdigest() != fingerprint:
                        raise RuntimeError('Transport request changed between attempts')
                    started = time.monotonic()
                    try:
                        with azure_request_slot(Path(trace) / 'api-concurrency.jsonl') if limited else nullcontext():
                            response = create(**kwargs)
                    except (APIConnectionError, APIStatusError) as error:
                        status = getattr(error, 'status_code', None)
                        transient = isinstance(error, APIConnectionError) or status == 429 or (status is not None and 500 <= status < 600)
                        retry = transient and attempt < len(delays)
                        record = {'call': self.calls, 'attempt': attempt+1, 'step': step,
                                  'request_sha256': fingerprint, 'scene_sha256': hashlib.sha256(scene).hexdigest(),
                                  'will_retry': retry, 'delay_seconds': delays[attempt] if retry else None,
                                  **failure_record(error, time.monotonic()-started), **self.provider.audit()}
                        with audit.open('a') as stream:
                            stream.write(json.dumps(record)+'\n'); stream.flush()
                        if not retry: raise
                        sleep(delays[attempt])
                    else:
                        if attempt:
                            with audit.open('a') as stream:
                                stream.write(json.dumps({'call': self.calls, 'attempt': attempt+1,
                                    'step': step, 'request_sha256': fingerprint,
                                    'status': 'succeeded_after_retry', **self.provider.audit()})+'\n')
                        return response

            self.client.responses.create = request
            (Path(trace)/'transport-policy.json').write_text(json.dumps({
                'scope': 'External evaluation adapter; frozen policy/robot executor unchanged',
                'max_retries': len(delays), 'delays_seconds': list(delays),
                'retry_only': ['connection_or_timeout', 'http429', 'http5xx'],
                'same_request_and_scene': True, 'robot_action_retries': 0,
                'provider_switching': False,
                'azure_request_limit': AZURE_LIMIT,
                'azure_shared_locks': str(AZURE_LOCKS)}, indent=2)+'\n')

    transport.NativeToolVision = RetryingVision


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--expected-commit', default='5e172339220b5a63b4d83a7c1b6220f9ff7f99d0')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--episode', type=Path, required=True)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--require-provider', choices=['azure-astra', 'zzz-astra', 'zzy-astra'])
    args = parser.parse_args()
    sys.path.insert(0, str(args.baseline))
    from egg_harness.freeze import verify
    from egg_harness import native_responses
    assert verify(args.baseline)['commit'] == args.expected_commit
    if args.require_provider is not None:
        assert json.loads(args.config.read_text())['astra_provider'] == args.require_provider
    install(native_responses, args.episode, required_provider=args.require_provider)
    sys.argv = ['egg_harness.skill_policy', '--config', str(args.config),
                '--episode', str(args.episode), '--trace', str(args.trace)]
    runpy.run_module('egg_harness.skill_policy', run_name='__main__')


if __name__ == '__main__': main()
