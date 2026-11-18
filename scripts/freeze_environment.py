"""Record the actual local evaluation fork and dependency identities, without secrets."""

import hashlib
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path


def repository(path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()
    return {'path': str(path), 'sha': git('rev-parse', 'HEAD'), 'status': git('status', '--porcelain'),
            'tracked_diff_sha256': hashlib.sha256(git('diff', 'HEAD').encode()).hexdigest()}


def freeze_environment(args):
    roots = {'RoboDojo': args.robodojo_root, 'XPolicyLab': args.robodojo_root / 'XPolicyLab',
             'assets': args.robodojo_root / '.cache/robodojo_assets_repo'}
    result = {'repositories': {name: repository(path) for name, path in roots.items()},
              'python': sys.version, 'packages': {name: importlib.metadata.version(name)
              for name in ('numpy', 'scipy', 'pydantic', 'torch')},
              'task': args.task, 'mode': args.mode, 'assets': str(args.assets),
              'object_assets': str(args.object_assets), 'single_environment': True, 'seed': 0}
    reference = 'ee67a1468510da7624a089164402359f2afc72c8'
    result['official_reference'] = reference
    available = subprocess.run(['git', '-C', str(args.robodojo_root), 'cat-file', '-e', reference + '^{commit}'],
                               capture_output=True, text=True, check=False)
    result['official_comparison'] = {'status': 'reference_unavailable' if available.returncode else 'compared',
                                     'pristine_official_environment_verified': False}
    if available.returncode:
        result['official_comparison']['reason'] = available.stderr.strip()
    else:
        result['official_comparison']['changed_files'] = subprocess.check_output(
            ['git', '-C', str(args.robodojo_root), 'diff', '--name-only', reference, 'HEAD'], text=True).splitlines()
    result['source_files'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in Path(__file__).resolve().parent.glob('*.py')}
    (args.output / 'environment-manifest.json').write_text(json.dumps(result, indent=2) + '\n')
