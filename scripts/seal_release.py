"""Explicit maintainer command to update the release checksum inventory."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ('frozen_runtime.py', 'release_resources.py', 'physevo_release.py',
           'check_portable_release.py', 'check_random_runtime_import.py', 'seal_release.py',
           'check_private_release.py')


def inventory(root):
    destination = root / 'release/manifest.json'
    paths = [p for folder in ('release', 'skills-upstream') for p in (root / folder).rglob('*')
             if p.is_file() and '__pycache__' not in p.parts and p != destination]
    paths.extend(root / 'scripts' / name for name in SCRIPTS)
    files = {}
    for path in sorted(paths):
        with path.open('rb') as stream:
            files[str(path.relative_to(root))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Update hashes after reviewing source changes')
    args = parser.parse_args()
    destination = ROOT / 'release/manifest.json'
    original = json.loads(destination.read_text())
    files = inventory(ROOT)
    if args.write:
        original['files'] = files
        destination.write_text(json.dumps(original, indent=2) + '\n')
    elif original['files'] != files:
        raise ValueError('Release inventory differs; inspect changes before using --write')
    print(f'Verified inventory: {len(files)} files')


if __name__ == '__main__':
    main()
