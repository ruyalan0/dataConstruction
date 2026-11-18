"""Read-only process and result stream for an existing layout campaign."""
import argparse
import json
import os
from pathlib import Path
import time

import five_layout_suite as suite


def observe(root, layout):
    folder = root / f'artifacts/five-layout-suite/L{layout}'
    campaign = suite.read(folder / 'campaign.json')
    assert os.environ['ARNOLD_WORKER_ID'] == campaign['worker']
    assert len(campaign['cases']) == 16
    pid = campaign['pid']
    proc = Path(f'/proc/{pid}')
    seen = set()
    while True:
        state = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[0] if proc.exists() else 'absent'
        if state not in ('absent', 'Z'):
            argv = (proc / 'cmdline').read_bytes().split(b'\0')
            assert any(value.endswith(b'/five_layout_suite.py') for value in argv)
            assert argv[argv.index(b'--layout') + 1] == str(layout).encode()
        calls = started = ended = archived = 0
        new_results = []
        for task, index, _ in campaign['cases']:
            assert index == layout
            case = folder / 'cases' / f'{task}-L{layout}-attempt1'
            if (case / 'status.json').exists():
                status = suite.read(case / 'status.json')
                calls += status['model_calls']
                started += status['model_calls'] > 0
            if (case / 'result.json').exists():
                ended += 1
                result = suite.read(case / 'result.json')
                final = (case / 'durability.json').exists() and suite.read(case / 'durability.json')['final']
                archived += final
                key = (task, final)
                if key not in seen:
                    new_results.append({'task': task, 'native': result.get('native'),
                                        'status': result['status'], 'final_archive': final})
                    seen.add(key)
        print(json.dumps({'at': suite.runner.now(), 'layout': layout, 'pid': pid,
                          'process_state': state, 'started': started, 'ended': ended,
                          'finally_archived': archived, 'model_calls_observed': calls,
                          'new_results': new_results}), flush=True)
        if state in ('absent', 'Z'):
            break
        time.sleep(30)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--layout', type=int, choices=suite.LAYOUTS, required=True)
    args = parser.parse_args()
    observe(args.root.resolve(), args.layout)
