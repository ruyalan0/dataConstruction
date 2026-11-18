"""Reuse the original simulator/launcher with an audited, external policy ablation."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import run_release_pickup_local as original
from pickup_geometry_ablation_policy import ABLATION

ROOT = Path(__file__).resolve().parents[1]
POLICY = Path(__file__).with_name('pickup_geometry_ablation_policy.py')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, choices=(20, 21, 22), required=True)
    parser.add_argument('--gpu', type=int, default=0)
    parser.add_argument('--episode-timeout', type=int, default=1800)
    parser.add_argument('--api-config', type=Path, default=Path('/home/lanruya/.codex/config.toml'))
    parser.add_argument('--api-auth', type=Path, default=Path('/home/lanruya/.codex/auth.json'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    args.development_case_reason = 'User-requested geometry assistance ablation on original L20/L21/L22'
    if args.output is None:
        stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
        args.output = ROOT / 'runs' / f'pickup-no-geometry-{stamp}-L{args.seed}'
    if args.gpu < 0 or args.episode_timeout <= 0:
        parser.error('Use a nonnegative GPU and a positive timeout')
    os.environ.update(
        ISAACSIM_PORTABLE_ROOT=str(ROOT.parent / '.runtime/isaacsim'),
        PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
    )
    count = int(os.environ.get('GIT_CONFIG_COUNT', '0'))
    for checkout in (ROOT, ROOT / 'dependencies/RoboDojo', ROOT / 'dependencies/RoboDojo/XPolicyLab'):
        os.environ[f'GIT_CONFIG_KEY_{count}'] = 'safe.directory'
        os.environ[f'GIT_CONFIG_VALUE_{count}'] = str(checkout)
        count += 1
    os.environ['GIT_CONFIG_COUNT'] = str(count)
    wrapper_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in (Path(__file__), POLICY)}
    save_original = original.save

    def audited_save(path, value):
        if path.name == f'config-{args.seed}.json':
            value = {**value, 'ablation': ABLATION}
        elif path.name == 'launch-record.json':
            value = {**value, 'ablation': ABLATION,
                     'scope': 'Original sealed simulator and policy with external in-memory geometry ablation',
                     'ablation_wrapper_sha256': wrapper_hashes}
        elif path.name == 'results.json':
            value = [{**item, 'ablation': ABLATION} for item in value]
        save_original(path, value)

    popen_original = original.subprocess.Popen

    def ablated_popen(command, *positional, **keywords):
        marker = ['-m', 'egg_harness.skill_policy']
        if isinstance(command, list) and command[2:4] == marker:
            command = [command[0], '-B', str(POLICY), '--harness-root',
                       str(original.HARNESS), *command[4:]]
        return popen_original(command, *positional, **keywords)

    original.save = audited_save
    original.subprocess.Popen = ablated_popen
    print('[ablation] ' + json.dumps(ABLATION), flush=True)
    try:
        with original.gpu_lease(args.gpu):
            return original.run(args)
    finally:
        original.save = save_original
        original.subprocess.Popen = popen_original


if __name__ == '__main__':
    raise SystemExit(main())
