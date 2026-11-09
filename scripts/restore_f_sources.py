"""Restore the exact RoboDojo and XPolicyLab sources from bundled Git objects."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / 'variants/astra_io_250hz_task_first_e_merged_20261105'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=ROOT / 'dependencies/RoboDojo')
    args = parser.parse_args()
    subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/check_f.py')], check=True)
    pins = json.loads((ROOT / 'release/dependencies.json').read_text())
    target = args.destination.resolve()
    if target.exists():
        raise FileExistsError('Use a new source destination: ' + str(target))
    for name in ('robodojo', 'xpolicylab'):
        pin = pins[name]
        bundle = ROOT / 'release' / pin['bundle']
        if hashlib.sha256(bundle.read_bytes()).hexdigest() != pin['bundle_sha256']:
            raise ValueError('Git bundle checksum mismatch: ' + name)
    environment = dict(os.environ, GIT_LFS_SKIP_SMUDGE='1')
    for name, folder in (('robodojo', target), ('xpolicylab', target / 'XPolicyLab')):
        pin = pins[name]
        subprocess.run(['git', 'clone', '--no-checkout', str(ROOT / 'release' / pin['bundle']),
                        str(folder)], env=environment, check=True)
        subprocess.run(['git', '-C', str(folder), 'checkout', '--detach', pin['commit']],
                       env=environment, check=True)
    sys.path.insert(0, str(VARIANT / 'harness/scripts'))
    from runtime_source import verify_runtime_source
    print(json.dumps(verify_runtime_source(target, pins['robodojo']['commit'],
          {'XPolicyLab': pins['xpolicylab']['commit']}), indent=2))


if __name__ == '__main__':
    main()
