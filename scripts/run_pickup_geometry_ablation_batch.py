"""Run one geometry-ablation attempt per original L20/L21/L22 layout."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

from pickup_geometry_ablation_policy import ABLATION

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpu', type=int, default=0)
    parser.add_argument('--episode-timeout', type=int, default=1800)
    parser.add_argument('--api-config', type=Path, default=Path('/home/lanruya/.codex/config.toml'))
    parser.add_argument('--api-auth', type=Path, default=Path('/home/lanruya/.codex/auth.json'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.gpu < 0 or args.episode_timeout <= 0:
        parser.error('Use a nonnegative GPU and a positive timeout')
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
    batch = (args.output or ROOT / 'runs' / f'ablation-no-geometry-{stamp}').resolve()
    batch.mkdir(parents=True, exist_ok=False)
    manifest = {
        'ablation': ABLATION, 'seeds': [20, 21, 22], 'gpu': args.gpu,
        'baseline': str(ROOT / 'runs/deployment-general-pickup-20261012-144559-J633A8'),
        'episodes': [], 'status': 'running',
    }

    def save():
        temporary = batch / 'batch.json.writing'
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(batch / 'batch.json')

    save()
    print('BATCH ' + str(batch), flush=True)
    for seed in manifest['seeds']:
        output = batch / f'L{seed}'
        command = [sys.executable, '-u', '-B', str(ROOT / 'scripts/run_pickup_geometry_ablation.py'),
                   '--seed', str(seed), '--gpu', str(args.gpu),
                   '--episode-timeout', str(args.episode_timeout), '--output', str(output),
                   '--api-config', str(args.api_config), '--api-auth', str(args.api_auth)]
        print(f'START L{seed}', flush=True)
        with (batch / f'launcher-L{seed}.log').open('w') as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        results_path = output / 'results.json'
        results = json.loads(results_path.read_text()) if results_path.exists() else []
        result = results[0] if results else {'status': 'launcher_failed', 'success': False}
        model = output / f'policy-{seed}/model'
        manifest['episodes'].append({
            'seed': seed, 'launcher_returncode': completed.returncode, 'result': result,
            'api_failures': len(list(model.glob('*.failure.json'))),
        })
        save()
        print(json.dumps({'seed': seed, 'status': result.get('status'),
                          'success': result.get('success'),
                          'score': result.get('official_observation', {}).get('score')},
                         ensure_ascii=False), flush=True)
        if not results_path.exists():
            manifest['status'] = 'infrastructure_error'
            save()
            return 2
    manifest['status'] = 'complete'
    save()
    print('COMPLETE ' + str(batch), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
