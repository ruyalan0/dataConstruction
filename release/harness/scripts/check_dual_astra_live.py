"""Run both paid native-tool protocol checks concurrently, without robot/GPU work."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import time

from egg_harness.astra_api import PROVIDERS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path('configs/classify-objects-multijoint-development.json'))
    parser.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh'], default='xhigh')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    def run(provider):
        log = args.output / (provider + '.log')
        start = time.time()
        with log.open('w') as stream:
            process = subprocess.run([sys.executable, '-m', 'scripts.check_native_tools_live',
                '--config', str(args.config), '--output', str(args.output / provider),
                '--provider', provider, '--effort', args.effort], stdout=stream, stderr=subprocess.STDOUT)
        return {'provider': provider, 'exit_code': process.returncode, 'started_at': start,
                'finished_at': time.time(), 'log': str(log)}

    with ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:
        results = list(pool.map(run, PROVIDERS))
    result = {'status': 'passed' if all(row['exit_code'] == 0 for row in results) else 'failed',
              'results': results, 'robot_steps': 0}
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    if result['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__': main()
