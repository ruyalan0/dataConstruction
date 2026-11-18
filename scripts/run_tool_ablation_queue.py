"""Wait for full-tool retry/merge, then run two independently reported ablations."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

from run_full_tool_task_batch import ROOT, read, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('queue', type=Path)
    args = parser.parse_args()
    queue = args.queue.resolve()
    plan = read(queue / 'queue.json')
    save(queue / 'status.json', dict(state='waiting_for_full_tools', waiting_unit=plan['full_tool_unit']))
    while True:
        service = subprocess.run(['systemctl', '--user', 'show', plan['full_tool_unit'],
                                  '--property=ActiveState', '--value'], text=True, capture_output=True)
        state = service.stdout.strip() if service.returncode == 0 else 'inactive'
        full_batch = Path(plan['full_tool_batch'])
        merge = read(Path(plan['merge_target']) / 'merge-record.json', {})
        completed = read(full_batch / 'summary.json', {}).get('final_audit_passed')
        if merge.get('rerun_batch') == str(full_batch) and completed:
            break
        if state not in ('active', 'activating'):
            if merge.get('rerun_batch') != str(full_batch) or not completed:
                save(queue / 'status.json', dict(state='blocked_on_full_tool_completion', service_state=state,
                                                reason='Full-tool four-task results have not passed audit and merged'))
                raise RuntimeError('Prerequisite full-tool run/merge incomplete; ablation phases have not started')
            break
        time.sleep(15)
    phase_errors = []
    for phase in plan['phases']:
        batch = Path(phase['batch'])
        save(queue / 'status.json', dict(state='running', mode=phase['mode'], batch=str(batch)))
        with (batch / 'runner.log').open('w') as log:
            result = subprocess.run([sys.executable, '-u', '-B', str(ROOT / 'scripts/run_tool_ablation_batch.py'),
                                     str(batch)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            phase_errors.append(dict(mode=phase['mode'], batch=str(batch), exit_code=result.returncode))
    save(queue / 'status.json', dict(state='complete_with_errors' if phase_errors else 'complete',
                                    phase_errors=phase_errors, completed_at=datetime.now(timezone.utc).isoformat(),
                                    reports=[str(Path(phase['batch']) / 'RESULT.md') for phase in plan['phases']]))


if __name__ == '__main__':
    main()
