"""Stop redundant full-score repeats, preserving raw plans and episode results."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal

from prepare_eight_eval import save
from run_eight_eval import now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    assert os.environ.get('ARNOLD_WORKER_ID') == '0000000'
    iteration = args.root/'artifacts/skill-iterations'/args.revision
    assert not (iteration/'scope-amendment.json').exists(), 'Scope already amended'
    plan = json.loads((iteration/'plan.json').read_text())
    supervisor = json.loads((iteration/'campaign.json').read_text())['pid']
    retained, excluded, handles = [], [], []
    for task, layout, _ in plan['cases']:
        baseline = args.root/'artifacts/eight-task-eval/cases'/f'{task}-L{layout}-attempt1'
        evidence = baseline/('result.json' if (baseline/'result.json').exists() else 'status.json')
        data = evidence.read_bytes()
        previous = json.loads(data)
        native = previous.get('native', {})
        full = native.get('native_terminal') and native.get('success') and native.get('score') == 100
        record = {'task': task, 'layout': layout, 'baseline_evidence': str(evidence),
                  'baseline_sha256': hashlib.sha256(data).hexdigest(), 'baseline_record': previous}
        if not full:
            record['reason'] = 'baseline_pending' if evidence.name == 'status.json' else 'baseline_not_full_score'
            retained.append(record)
            continue
        folder = iteration/'cases'/f"{task}-L{layout}-attempt{plan['attempt']}"
        record['reason'] = 'baseline_native_full_score'
        excluded.append(record)
        if (folder/'result.json').exists():
            record['action'] = 'already_ended_preserve_result'
            continue
        case = Path(json.loads((folder/'status.json').read_text())['worker_case'])
        expected_episode = str(case/'episode').encode()
        candidates = []
        for entry in Path('/proc').iterdir():
            if not entry.name.isdecimal(): continue
            try: argv = (entry/'cmdline').read_bytes().split(b'\0')
            except ProcessLookupError: continue  # A process can exit during /proc enumeration.
            except FileNotFoundError: continue
            if str(args.root/'scripts/eval_retry_policy.py').encode() not in argv: continue
            if expected_episode not in argv: continue
            candidates.append(int(entry.name))
        assert len(candidates) == 1, (folder.name, candidates)
        pid = candidates[0]
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        assert int(stat[1]) == supervisor, 'Policy is not owned by this campaign'
        stop = {'requested_at': now(), 'reason': record['reason'], 'policy_pid': pid,
                'supervisor_pid': supervisor, 'process_start_ticks': stat[19], 'case': folder.name,
                'baseline_sha256': record['baseline_sha256'], 'signal': 'SIGTERM',
                'intent': 'User requested reruns of previously non-full-score scenes only.'}
        handles.append((pid, folder, case, stop))
        record['action'] = 'stop_redundant_policy'
    assert retained and excluded, 'Expected a mixed cohort'
    save(iteration/'scope-amendment.json', {'at': now(), 'original_plan_preserved': True,
         'retained': retained, 'excluded': excluded,
         'method': 'Stop only owned policy processes; existing supervisors finalize simulators and archives.'})
    for pid, folder, case, stop in handles:
        save(folder/'administrative-stop.json', stop)
        save(case/'administrative-stop.json', stop)
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        assert int(stat[1]) == supervisor and stat[19] == stop['process_start_ticks'], 'Policy identity changed'
        os.kill(pid, signal.SIGTERM)
        print(json.dumps(stop), flush=True)
    print(json.dumps({'retained': len(retained), 'excluded': len(excluded), 'signals_sent': len(handles)}))


if __name__ == '__main__': main()
