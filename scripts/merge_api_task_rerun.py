"""Replace five interrupted aggregate rows with an audited user-requested rerun."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo

from run_full_tool_task_batch import ROOT, read, save, summarize


def recover_startup_rate_limits(rerun, manifest):
    """Retry a zero-action concurrency refusal once, after other workers finish."""
    refused = []
    for entry in manifest['episodes']:
        if entry['outcome'] != 'api_interruption' or entry['steps'] != 0 or entry.get('rate_limit_recovery'):
            continue
        records = [read(path) for path in (Path(entry['output']) / f'policy-{entry["layout"]}/model').glob('*.failure.json')]
        if any(r.get('http_status') == 429 and 'gateway_concurrency_limit' in r.get('message', '') for r in records):
            refused.append(entry)
    if not refused:
        return manifest
    snapshot = rerun / 'history' / ('before-serial-rate-limit-recovery-' + datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S'))
    snapshot.mkdir(parents=True, exist_ok=False)
    for name in ('batch.json', 'summary.json', 'RESULT.md', 'final-audit.json'):
        shutil.copy2(rerun / name, snapshot / name)
    environment = dict(os.environ, ISAACSIM_PORTABLE_ROOT=str(ROOT.parent / '.runtime/isaacsim'),
                       PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    count = int(environment.get('GIT_CONFIG_COUNT', '0'))
    for checkout in (ROOT, ROOT / 'dependencies/RoboDojo', ROOT / 'dependencies/RoboDojo/XPolicyLab'):
        environment[f'GIT_CONFIG_KEY_{count}'] = 'safe.directory'
        environment[f'GIT_CONFIG_VALUE_{count}'] = str(checkout)
        count += 1
    environment['GIT_CONFIG_COUNT'] = str(count)
    for previous in refused:
        task, layout, gpu = previous['task'], previous['layout'], previous['gpu']
        inventory = subprocess.check_output(['nvidia-smi', '--query-gpu=index,memory.used,utilization.gpu',
                                            '--format=csv,noheader,nounits'], text=True)
        idle = [int(values[0]) for line in inventory.splitlines()
                if (values := [v.strip() for v in line.split(',')]) and int(values[1]) <= 512 and int(values[2]) <= 5]
        if gpu not in idle:
            gpu = next(index for index in manifest['gpus'] if index in idle)
        output = rerun / task / 'after-startup-concurrency-refusal' / f'L{layout}'
        output.parent.mkdir(parents=True, exist_ok=True)
        manifest.update(status='recovering_rate_limit', active=[dict(task=task, layout=layout, gpu=gpu, output=str(output))])
        save(rerun / 'batch.json', manifest)
        print(f'RECOVER ZERO-ACTION 429 SERIAL: {task} L{layout}', flush=True)
        command = [str(ROOT / '.venv-policy/bin/python'), '-u', '-B', str(ROOT / 'scripts/run_release_task_local.py'),
            '--task', task, '--task-config', str(rerun / f'configs/{task}.json'), '--seed', str(layout), '--gpu', str(gpu),
            '--episode-timeout', str(manifest['episode_timeout']),
            '--development-case-reason', 'Serial recovery after zero-action gateway concurrency refusal',
            '--api-config', '/home/lanruya/.codex/config.toml', '--api-auth', '/home/lanruya/.codex/auth.json',
            '--output', str(output)]
        with (rerun / f'{task}-L{layout}.serial-recovery.launcher.log').open('w') as log:
            completed = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        replacement = summarize(output, task, layout, gpu, completed.returncode, set(manifest['full_tools']))
        replacement.update(rate_limit_recovery=True, attempt_number=2, previous_attempt_output=previous['output'])
        manifest.setdefault('superseded_attempts', []).append(previous)
        manifest['episodes'] = [replacement if e['task'] == task and e['layout'] == layout else e for e in manifest['episodes']]
        manifest['initial_attempts_per_task_layout'] = manifest.pop('attempts_per_task_layout', 1)
        manifest['attempt_counts'] = {f'{e["task"]}/L{e["layout"]}': e.get('attempt_number', 1) for e in manifest['episodes']}
        manifest['total_attempts'] = len(manifest['episodes']) + len(manifest['superseded_attempts'])
        manifest.update(status='complete', active=[], finished_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
        save(rerun / 'batch.json', manifest)
        print(f'END SERIAL RECOVERY: {task} {replacement["outcome"]}', flush=True)
    subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/audit_full_tool_task_batch.py'), str(rerun)], check=True)
    manifest = read(rerun / 'batch.json')
    summary = read(rerun / 'summary.json')
    summary.update(total_attempts=len(manifest['episodes']) + len(manifest['superseded_attempts']),
                   historical_api_errors=sum(e['api_errors'] for e in manifest['superseded_attempts']))
    save(rerun / 'summary.json', summary)
    with (rerun / 'RESULT.md').open('a') as report:
        report.write(f'\n## 启动并发拒绝后的单独补跑\n\n{len(refused)} 项在执行动作前被 gateway_concurrency_limit（HTTP 429）拒绝，等待其它任务结束后按相同配置单独补跑一次。SDK max_retries 保持 0。初次拒绝记录保留在原目录和 batch.json 的 superseded_attempts，补跑结果作为最新结果；共记录 {summary["total_attempts"]} 次启动尝试。\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path)
    parser.add_argument('rerun', type=Path)
    args = parser.parse_args()
    target, rerun = args.target.resolve(), args.rerun.resolve()
    manifest = read(target / 'batch.json')
    replacement = read(rerun / 'batch.json')
    assert manifest['status'] == replacement['status'] == 'complete'
    assert read(rerun / 'summary.json')['final_audit_passed']
    assert read(rerun / 'final-audit.json')['passed']
    assert hashlib.sha256((target / 'batch.json').read_bytes()).hexdigest() == replacement['merge_target_manifest_sha256']
    replacement = recover_startup_rate_limits(rerun, replacement)
    for key in ('layouts', 'seed_group', 'model', 'reasoning_effort', 'action_interface',
                'disabled_tools', 'full_tools', 'episode_timeout', 'explicit_episode_memory',
                'model_history_replay', 'rgb_memory_enabled', 'original_sdk_max_retries',
                'endpoint'):
        assert manifest[key] == replacement[key], key
    selected = {(e['task'], e['layout']) for e in manifest['episodes'] if e['outcome'] == 'api_interruption'}
    updates = {(e['task'], e['layout']): e for e in replacement['episodes']}
    assert selected == set(updates) and selected
    for task, layout in selected:
        old_config = read(target / f'configs/{task}.json')
        new_config = read(rerun / f'configs/{task}.json')
        assert {key: value for key, value in new_config.items() if key != 'transport_retry'} == {
            key: value for key, value in old_config.items() if key != 'transport_retry'}
        if new_config.get('transport_retry') != old_config.get('transport_retry'):
            assert replacement.get('transport_retry') == new_config.get('transport_retry')
    assert hashlib.sha256((target / 'batch.json').read_bytes()).hexdigest() == replacement['merge_target_manifest_sha256']
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
    history = target / 'history' / f'before-api-rerun-merge-{stamp}'
    history.mkdir(parents=True)
    for name in ('batch.json', 'summary.json', 'RESULT.md', 'final-audit.json',
                 'final-integrity-check.log', 'api-failure-summary.json', 'exit-codes.tsv'):
        if (target / name).exists():
            shutil.copy2(target / name, history / name)
    old_rows = [e for e in manifest['episodes'] if (e['task'], e['layout']) in selected]
    shutil.copytree(target / 'configs', history / 'configs')
    old_launcher = manifest['launcher_sha256']
    accepted = set(manifest.get('accepted_launcher_sha256', [old_launcher]))
    accepted.add(replacement['launcher_sha256'])
    manifest['accepted_launcher_sha256'] = sorted(accepted)
    manifest.setdefault('superseded_attempts', []).extend(old_rows)
    extra_attempts = replacement.get('superseded_attempts', [])
    manifest['superseded_attempts'].extend(dict(e, attempt_number=2, rerun_batch=str(rerun)) for e in extra_attempts)
    merged = []
    for old in manifest['episodes']:
        key = (old['task'], old['layout'])
        if key in updates:
            attempt = old.get('attempt_number', 1) + 1 + sum(e['task'] == old['task'] and e['layout'] == old['layout'] for e in extra_attempts)
            new = dict(updates[key], attempt_number=attempt, rerun_batch=str(rerun),
                       previous_attempt_output=old['output'])
            merged.append(new)
            shutil.copy2(rerun / f'configs/{old["task"]}.json', target / f'configs/{old["task"]}.json')
            for prepared in manifest['prepared']:
                if prepared['task'] == old['task'] and prepared['layout'] == old['layout']:
                    prepared['config_sha256'] = hashlib.sha256((target / f'configs/{old["task"]}.json').read_bytes()).hexdigest()
        else:
            merged.append(dict(old, launcher_sha256=old.get('launcher_sha256') or old_launcher))
    manifest['episodes'] = merged
    manifest['launcher_sha256'] = replacement['launcher_sha256']
    manifest['initial_attempts_per_task_layout'] = manifest.pop('attempts_per_task_layout', 1)
    manifest['attempt_counts'] = {f'{e["task"]}/L{e["layout"]}': e.get('attempt_number', 1) for e in merged}
    manifest['total_attempts'] = len(merged) + len(manifest['superseded_attempts'])
    manifest['merged_at'] = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    manifest.setdefault('rerun_merges', []).append(dict(batch=str(rerun), tasks=replacement['tasks'],
        previous_summary=str(history), merged_at=manifest['merged_at']))
    save(target / 'batch.json', manifest)
    subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/audit_full_tool_task_batch.py'), str(target)], check=True)
    manifest = read(target / 'batch.json')
    summary = read(target / 'summary.json')
    all_attempts = manifest['episodes'] + manifest['superseded_attempts']
    summary.update(total_attempts=manifest['total_attempts'], current_episodes=len(merged),
        superseded_attempts=len(manifest['superseded_attempts']),
        historical_api_errors=sum(e['api_errors'] for e in manifest['superseded_attempts']),
        all_attempt_api_errors=sum(e['api_errors'] for e in all_attempts),
        all_attempt_requests=sum(e['requests'] for e in all_attempts),
        all_attempt_completed_responses=sum(e['completed_responses'] for e in all_attempts),
        rerun_batch=str(rerun), merged_at=manifest['merged_at'])
    save(target / 'summary.json', summary)
    failures = []
    for entry in manifest['episodes']:
        for path in sorted((Path(entry['output']) / f'policy-{entry["layout"]}/model').glob('*.failure.json')):
            failure = read(path)
            failures.append(dict(task=entry['task'], type=failure['type'], http_status=failure.get('http_status'),
                                 message=failure.get('message', '')[:240], record=str(path)))
    save(target / 'api-failure-summary.json', failures)
    report = (target / 'RESULT.md').read_text().replace(
        '无自动重跑或结果覆盖。',
        f'按用户要求将 {len(selected)} 个 API 中断任务各重跑一次，汇总采用最新结果，全部原始记录保留。')
    report = report.replace('每场记录保存在 <task>/L<layout>/：',
        '每场记录路径以 summary.json 的 output 为准；重跑记录位于重跑批次，其余记录保持原路径。各场保存：')
    report += '\n## API 中断任务重跑合并\n\n'
    report += f'当前汇总 {len(merged)} 个任务的最新结果，共保留 {summary["total_attempts"]} 次实验记录。本次替换 {len(selected)} 个任务，启动并发拒绝后的补跑 {len(extra_attempts)} 场。当前 API 错误 {summary["api_errors"]} 次，历史已替换场次 API 错误 {summary["historical_api_errors"]} 次。历史中断记录不计作最新任务结果，也未删除。\n\n'
    report += f'重跑批次：[结果]({rerun / "RESULT.md"})；合并前快照：[结果]({history / "RESULT.md"})。结构化历史见 batch.json 的 superseded_attempts。\n\n'
    report += '| 任务 | 原结果 | 最新结果 | 最新分数 | 最新记录 |\n| --- | --- | --- | --- | --- |\n'
    for old in old_rows:
        new = next(e for e in manifest['episodes'] if e['task'] == old['task'] and e['layout'] == old['layout'])
        score = str(new['native_score']) if new['native_terminal'] else f'中止观测 {new["native_score"]}'
        report += f'| {old["task"]} | API 中断 | {new["outcome"]} | {score} | [JSON]({Path(new["output"]) / "results.json"}) |\n'
    (target / 'RESULT.md').write_text(report)
    save(target / 'merge-record.json', dict(rerun_batch=str(rerun), previous_snapshot=str(history),
        replaced_tasks=replacement['tasks'], unchanged_tasks=[e['task'] for e in merged if (e['task'], e['layout']) not in selected],
        latest_episode_count=len(merged), total_attempt_count=summary['total_attempts'],
        preserved_original_outputs=[e['output'] for e in old_rows], final_audit_passed=True))
    print('MERGED ' + json.dumps(dict(target=str(target), outcomes=dict(Counter(e['outcome'] for e in merged)),
                                    total_attempts=summary['total_attempts']), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
