"""Verify that this experiment leaves its parent and frozen release unchanged."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def verify():
    hashes = json.loads((HERE / 'originals-sha256.json').read_text())
    changed = [name for name, expected in hashes.items() if not (ROOT / name).is_file()
               or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected]
    return dict(passed=not changed, protected_files=len(hashes), differences=changed)


if __name__ == '__main__':
    report = verify()
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] else 1)
