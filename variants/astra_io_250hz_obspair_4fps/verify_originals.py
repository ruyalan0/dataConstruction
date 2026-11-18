"""Verify all protected code and the supplied val.jsonl remain unchanged."""
import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def verify():
    hashes = json.loads((HERE / 'audit/protected-originals-sha256.json').read_text())
    changed = [name for name, digest in hashes.items() if not (ROOT / name).is_file()
               or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    parent = runpy.run_path(str(HERE.parent / 'astra_io_250hz_obspair/verify_originals.py'))['verify']()
    return dict(passed=not changed and parent['passed'], protected_files=len(hashes),
                differences=changed, parent_integrity=parent)


if __name__ == '__main__':
    result = verify()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
