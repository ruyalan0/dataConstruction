"""Check real frozen Harness acceptance and rejection without API or robot calls."""
import argparse
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import diagnose_suite as base
from verify_provider_harness import verify_runtime


def check(harness):
    assert verify_runtime(base.HARNESS)['zzy_supported'] is False
    assert verify_runtime(harness)['zzy_supported'] is True
    from egg_harness.freeze import digest

    def rejected(candidate, error_type):
        try:
            verify_runtime(candidate)
        except error_type:
            return
        raise AssertionError('Invalid Harness accepted')

    with TemporaryDirectory(prefix='zzy-harness-check-') as temporary:
        candidate = Path(temporary) / 'candidate'
        shutil.copytree(harness, candidate, ignore=shutil.ignore_patterns('__pycache__'))
        rejected(candidate, FileNotFoundError)
        receipt = candidate.with_suffix('.qualification.json')
        shutil.copyfile(harness.with_suffix('.qualification.json'), receipt)
        assert verify_runtime(candidate)['zzy_supported'] is True
        manifest_path = candidate / 'manifest.json'
        original_manifest = json.loads(manifest_path.read_text())
        manifest_path.chmod(0o644)
        for name in ('scripts/serve.py', 'egg_harness/astra_api.py'):
            path = candidate / name
            content = path.read_bytes()
            path.chmod(0o644)
            path.write_bytes(content + b'\n# changed fixture\n')
            rejected(candidate, RuntimeError)  # Unrecorded file edit.
            altered = {**original_manifest, 'files': digest(candidate)}
            manifest_path.write_text(json.dumps(altered))
            rejected(candidate, AssertionError)  # Recomputed manifest cannot approve new code.
            path.write_bytes(content)
            manifest_path.write_text(json.dumps(original_manifest, indent=2))
        # Preserve the original manifest's exact bytes for the receipt's hash.
        shutil.copyfile(harness / 'manifest.json', manifest_path)
        qualification = json.loads(receipt.read_text())
        qualification['preflight_result_sha256'] = 'invalid'
        receipt.write_text(json.dumps(qualification))
        rejected(candidate, AssertionError)
    print('PASS: original and qualified Harness accepted; missing preflight, edited runtime, rewritten manifest and invalid receipt rejected')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--harness', type=Path, required=True)
    check(parser.parse_args().harness.resolve())
