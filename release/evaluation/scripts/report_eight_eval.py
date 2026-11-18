"""Summarize NAS-mirrored evaluation evidence without simulator introspection."""
import argparse
import json
from pathlib import Path
from prepare_eight_eval import TASKS, save


def end_reason(folder, result):
    native = result.get('native', {})
    if native.get('valid_sample') is False or native.get('unstable') is True:
        return 'invalid_native_sample'
    if native.get('native_terminal'):
        return 'native_success' if native['success'] else 'native_unsuccessful_end'
    path = folder/'policy.log.tail'
    log = path.read_text(errors='replace') if path.exists() else ''
    if 'RuntimeError: Astra stopped:' in log: return 'astra_stopped'
    if 'ValueError: Arm joint limit exceeded' in log: return 'harness_joint_boundary'
    if 'Model call budget exhausted' in log: return 'model_call_budget_exhausted'
    if 'quota_not_enough' in log: return 'provider_quota_exhausted'
    return result['status']


def outcome_text(native, reason):
    if native is None:
        return reason
    if native.get('valid_sample') is False or native.get('unstable') is True:
        assert reason == 'invalid_native_sample'
        return 'unscored; invalid_native_sample'
    assert native['score'] is not None, 'Valid native result is missing its score'
    return f"{native['score']:g}{'' if native['native_terminal'] else ' prefix'}; {reason}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--campaign', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((args.campaign/'campaign.json').read_text())
    rows = []
    for task in TASKS:
        row = {'task': task, 'cases': []}
        for index in config['indices']:
            folder = args.campaign/'cases'/f'{task}-L{index}-attempt1'
            record = {'layout': index, 'phase': 'queued'}
            if (folder/'simulator.log.tail').exists(): record['phase'] = 'starting'
            if (folder/'status.json').exists():
                record.update(json.loads((folder/'status.json').read_text()))
                record['phase'] = 'running'
            if (folder/'result.json').exists():
                result = json.loads((folder/'result.json').read_text())
                record.update(result); record['phase'] = 'finished'
                record['end_reason'] = end_reason(folder, result)
            if (folder/'durability.json').exists(): record['durability'] = json.loads((folder/'durability.json').read_text())
            row['cases'].append(record)
        rows.append(row)
    total = sum(len(row['cases']) for row in rows)
    finished = sum(case['phase']=='finished' for row in rows for case in row['cases'])
    report = {'source_commit': config['source_commit'], 'action_interface': 'joint',
        'case_count': total, 'finished': finished, 'rows': rows,
        'scope': 'Two native-initial capability cases per task; no population success-rate estimate.'}
    save(args.campaign/'summary.json', report)
    lines = ['# Eight-task evaluation at5e17233', '', f'Finished: {finished}/{total}. Joint-only; native initial states.', '',
             '| Task | L20 | L21 |', '|---|---|---|']
    for row in rows:
        cells = []
        for case in row['cases']:
            if 'native' in case:
                native = case['native']
                text = outcome_text(native, case['end_reason'])
                if case.get('end_reason') == 'astra_stopped':
                    text += ' / Astra stopped incomplete'
                elif case.get('status') != 'policy_completed' and case['end_reason'] != 'invalid_native_sample':
                    text += ' / interrupted: '+case['end_reason']+' (prefix score only)'
            elif case['phase']=='finished': text = case['status']+' (no native verdict)'
            elif case['phase']=='running': text = f"step {case['step']}, calls {case['model_calls']}"
            else: text = case['phase']
            cells.append(text)
        lines.append('| '+row['task']+' | '+' | '.join(cells)+' |')
    lines.extend(['', 'Scores come from native verdicts, not model self-reports. Infrastructure errors remain separately identified.'])
    (args.campaign/'summary.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))


if __name__ == '__main__': main()
