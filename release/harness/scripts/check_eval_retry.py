"""Exercise retry boundaries without model calls or robot execution."""
from pathlib import Path
import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import httpx2 as httpx
from openai import RateLimitError, BadRequestError
from eval_retry_policy import install


def error(kind):
    status = 429 if kind is RateLimitError else 400
    return kind('fixture', response=httpx.Response(status, request=httpx.Request('POST', 'https://example.test/v1/responses')), body={})


def exercise(errors, expected, change_scene=False):
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        scene = root/'latest.json'; scene.write_text('{"step":7}')
        requests = []
        def create(**kwargs):
            requests.append(json.dumps(kwargs, sort_keys=True))
            if errors: raise errors.pop(0)
            return 'response'
        class FakeVision:
            def __init__(self, config, trace):
                self.calls = 1
                self.provider = SimpleNamespace(audit=lambda: {'provider': 'fixture'})
                self.client = SimpleNamespace(responses=SimpleNamespace(create=create))
        module = SimpleNamespace(NativeToolVision=FakeVision)
        def sleep(_):
            if change_scene: scene.write_text('{"step":8}')
        install(module, root, delays=(0, 0, 0), sleep=sleep)
        client = module.NativeToolVision({}, root)
        failed = False
        try: result = client.client.responses.create(model='fixture', input=[{'role':'user','content':'same'}])
        except (RateLimitError, BadRequestError, RuntimeError): failed = True
        assert len(requests) == expected
        assert len(set(requests)) == 1
        records = [json.loads(line) for line in (root/'transport-retries.jsonl').read_text().splitlines()]
        assert records[0]['step'] == 7
        return failed, records


failed, records = exercise([error(RateLimitError)], 2)
assert not failed and records[-1]['status'] == 'succeeded_after_retry'
failed, records = exercise([error(RateLimitError) for _ in range(5)], 4)
assert failed and records[-1]['will_retry'] is False
failed, records = exercise([error(BadRequestError)], 1)
assert failed and records[0]['will_retry'] is False
failed, records = exercise([error(RateLimitError)], 1, change_scene=True)
assert failed
print('PASS: identical payload, bounded429 retries, no400 retry, changed-scene rejection')

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    class ProviderFixture:
        def __init__(self, config, trace):
            self.provider = SimpleNamespace(name=config['actual_provider'], audit=lambda: {'provider': config['actual_provider']})
            self.client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: 'ok'))
    module = SimpleNamespace(NativeToolVision=ProviderFixture)
    install(module, root, required_provider='azure-astra')
    module.NativeToolVision({'actual_provider':'azure-astra'}, root)
    try:
        module.NativeToolVision({'actual_provider':'zzz-astra'}, root)
    except AssertionError as error:
        assert str(error) == 'Unexpected actual API provider'
    else:
        raise AssertionError('Non-Azure provider passed the mandatory guard')
print('PASS: actual Azure provider accepted; non-Azure provider rejected before requests')

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    (root / 'latest.json').write_text('{"step":0}')
    class ZzyFixture(ProviderFixture):
        def __init__(self, config, trace):
            super().__init__(config, trace)
            self.calls = 1
    module = SimpleNamespace(NativeToolVision=ZzyFixture)
    install(module, root, required_provider='zzy-astra')
    with patch('eval_retry_policy.azure_request_slot', side_effect=AssertionError('ZZY acquired Azure slot')):
        model = module.NativeToolVision({'actual_provider': 'zzy-astra'}, root)
        assert model.client.responses.create(model='gpt-6-astra', input=[]) == 'ok'
        for wrong in ('azure-astra', 'zzz-astra'):
            try:
                module.NativeToolVision({'actual_provider': wrong}, root)
            except AssertionError as error:
                assert str(error) == 'Unexpected actual API provider'
            else:
                raise AssertionError('Provider mismatch was accepted')
print('PASS: ZZY pinned; mismatched providers rejected; Azure slots untouched')
