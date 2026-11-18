"""Prove that protected original source files and the audited historical logs are unchanged."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def verify():
    originals = json.loads((HERE / 'audit/original-code-sha256.json').read_text())
    logs = json.loads((HERE / 'audit/duplicates.json').read_text())['source_sha256']
    differences = []
    for name, expected in {**originals, **logs}.items():
        path = ROOT / name
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != expected:
            differences.append(name)
    return dict(passed=not differences, original_source_files=len(originals),
                historical_log_files=len(logs), differences=differences)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = verify()
    if args.output:
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
