"""Verify the frozen parents and the portable f distribution without a GPU or API."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / 'variants/astra_io_250hz_task_first_e_merged_20261105'
MANIFEST = ROOT / 'provenance/f-portable-sha256.json'


def inventory():
    paths = [path for path in VARIANT.rglob('*')
             if path.is_file() and '__pycache__' not in path.parts]
    paths.extend(ROOT / name for name in (
        'scripts/run_f.py', 'scripts/check_f.py', 'scripts/restore_f_sources.py',
        'scripts/full_tool_retry_policy.py', 'scripts/export_io_variant_ms_swift.py',
        'requirements-policy.txt', 'examples/api.toml', 'provenance/seven-task-equivalence.json',
        'provenance/f-original-sha256.json', 'provenance/additional-task-configs.json'))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-manifest', action='store_true',
                        help='Explicitly reseal the portable files after reviewing changes')
    args = parser.parse_args()
    sys.path.insert(0, str(VARIANT))
    from verify_originals import verify, verify_release
    protected = verify()
    if not protected['passed']:
        raise ValueError('Frozen source changed: ' + ', '.join(protected['differences']))
    verify_release()
    actual = inventory()
    if args.write_manifest:
        MANIFEST.write_text(json.dumps(actual, indent=2, sort_keys=True) + '\n')
    expected = json.loads(MANIFEST.read_text())
    differences = sorted(name for name in set(actual) | set(expected)
                         if actual.get(name) != expected.get(name))
    if differences:
        raise ValueError('Portable f files differ: ' + ', '.join(differences))
    original = json.loads((ROOT / 'provenance/f-original-sha256.json').read_text())
    changed = sorted(name for name, digest in original.items()
                     if actual.get(str(VARIANT.relative_to(ROOT) / name)) != digest)
    print(json.dumps(dict(passed=True, frozen_parent_files=protected['protected_files'],
                          portable_files=len(actual), changes_from_original_f=changed), indent=2))


if __name__ == '__main__':
    main()
