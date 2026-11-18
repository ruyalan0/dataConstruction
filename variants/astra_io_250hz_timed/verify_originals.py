"""Check the preceding 250 Hz variant and its protected original sources/logs."""
import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def verify():
    original = runpy.run_path(str(HERE.parent / 'astra_io_250hz/verify_originals.py'))['verify']()
    hashes = json.loads((HERE / 'audit/previous-250hz-sha256.json').read_text())
    differences = [name for name, expected in hashes.items()
                   if not (ROOT / name).is_file()
                   or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected]
    return dict(passed=original['passed'] and not differences,
                previous_variant_files=len(hashes), previous_variant_differences=differences,
                original_sources_and_logs=original)


if __name__ == '__main__':
    result = verify()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
