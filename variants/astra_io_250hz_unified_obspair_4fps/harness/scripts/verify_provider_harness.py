"""Verify a frozen provider extension against the existing approved robot runtime."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

import diagnose_suite as base


def verify_runtime(harness, require_preflight=True):
    harness = harness.resolve()
    sys.path.insert(0, str(base.HARNESS))
    from egg_harness.freeze import verify
    original, candidate = verify(base.HARNESS), verify(harness)
    assert original['commit'] == base.HARNESS_COMMIT
    names = {name for name in set(original['files']) | set(candidate['files'])
        if name.startswith(('egg_harness/', 'configs/'))}
    names.update('scripts/' + name for name in ('serve.py', 'server_loop.py', 'worker_driver.py', 'native_setup.py'))
    changed = sorted(name for name in names if original['files'].get(name) != candidate['files'].get(name))
    same = harness == base.HARNESS.resolve()
    assert changed == ([] if same else ['egg_harness/astra_api.py']), changed
    if not same:
        source = subprocess.check_output(['git', '-C', str(Path(__file__).resolve().parents[1]),
            'show', candidate['commit'] + ':egg_harness/astra_api.py'])
        assert source == (harness / 'egg_harness/astra_api.py').read_bytes(), 'Provider differs from committed source'
        if require_preflight:
            qualification = json.loads(harness.with_suffix('.qualification.json').read_text())
            assert qualification['status'] == 'passed'
            assert qualification['harness_manifest_sha256'] == base.digest(harness / 'manifest.json')
            receipt = Path(qualification['preflight_result'])
            assert base.digest(receipt) == qualification['preflight_result_sha256']
            result = json.loads(receipt.read_text())
            assert result['status'] == 'passed' and result['provider'] == 'zzy-astra'
            assert result['api'] == 'responses' and result['model'] == 'gpt-6-astra'
            assert result['base_url'] == 'https://zzy.example.invalid' and result['model_calls'] == 3
            assert result['effort'] == 'high'
            assert result['harness']['manifest_sha256'] == base.digest(harness / 'manifest.json')
            assert result['native_steps'] == 0 and result['provider_switching'] is False
    return {'commit': candidate['commit'], 'baseline_commit': original['commit'],
        'changed_runtime_files': changed, 'zzy_supported': not same,
        'manifest_sha256': base.digest(harness / 'manifest.json'),
        'robot_tools_and_task_configs_unchanged': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--harness', type=Path, required=True)
    print(json.dumps(verify_runtime(parser.parse_args().harness)))
