"""Finalize full-tool task evidence, including explicit timeout attribution."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess

from run_full_tool_task_batch import ROOT, read, save, write_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', type=Path)
    args = parser.parse_args()
    batch = args.batch.resolve()
    manifest = read(batch / 'batch.json')
    assert manifest['status'] == 'complete' and not manifest['active']
    planned = {(task, layout) for task in manifest['tasks'] for layout in manifest['layouts']}
    assert {(e['task'], e['layout']) for e in manifest['episodes']} == planned
    expected = {'control_arms', 'review_frames', 'project_pixels', 'triangulate_point',
                'triangulate_temporal_point', 'verify_completion', 'stop_episode'}
    assert set(manifest['full_tools']) == expected
    count = int(os.environ.get('GIT_CONFIG_COUNT', '0'))
    for checkout in (ROOT, ROOT / 'dependencies/RoboDojo', ROOT / 'dependencies/RoboDojo/XPolicyLab'):
        os.environ[f'GIT_CONFIG_KEY_{count}'] = 'safe.directory'
        os.environ[f'GIT_CONFIG_VALUE_{count}'] = str(checkout)
        count += 1
    os.environ['GIT_CONFIG_COUNT'] = str(count)
    from physevo_release import verify, source_check
    pins = verify()
    source_check(ROOT / 'dependencies/RoboDojo', pins)
    tasks = Path(ROOT / 'dependencies/RoboDojo/task/RoboDojo/tasks')
    audit = []
    for entry in manifest['episodes']:
        output = Path(entry['output'])
        task, layout = entry['task'], entry['layout']
        result = read(output / 'results.json')[0]
        launch = read(output / 'launch-record.json')
        config = read(output / f'config-{layout}.json')
        prepared = read(batch / f'configs/{task}.json')
        native = result.get('official_observation', {})
        assert result['task'] == task and result['seed_index'] == layout
        assert result['release_unchanged'] and result['human_interventions'] == 0
        assert launch['original_sdk_max_retries'] == 0 and 'ablation' not in config
        expected_launcher = entry.get('launcher_sha256') or manifest['launcher_sha256']
        assert launch['launcher_sha256'] == expected_launcher
        assert expected_launcher in manifest.get('accepted_launcher_sha256', [manifest['launcher_sha256']])
        if manifest['episode_timeout'] == 0:
            assert launch['episode_timeout_seconds'] is None
            assert result['episode_timeout_seconds'] is None
        for key in ('model', 'reasoning_effort', 'request_timeout', 'max_model_calls',
                    'task', 'skill_names', 'action_interface', 'public_task_requirements'):
            assert config[key] == prepared[key], (task, key)
        assert config['model'] == 'gpt-6-astra' and config['reasoning_effort'] == 'high'
        assert config['provider_base_urls']['zzy-astra'] == manifest['endpoint']
        assert config['robodojo_revision'] == pins['robodojo']['commit']
        assert config['robodojo_required_submodules']['XPolicyLab'] == pins['xpolicylab']['commit']
        assert config.get('transport_retry') == prepared.get('transport_retry')
        if config.get('transport_retry'):
            assert launch['external_transport_retry'] == config['transport_retry']
            assert launch['retry_wrapper_sha256'] == hashlib.sha256((ROOT / 'scripts/full_tool_retry_policy.py').read_bytes()).hexdigest()
        assert not Path(config['zzy_credentials']).exists()
        layout_record = read(output / 'layout-provenance.json')
        assert hashlib.sha256(Path(layout_record['path']).read_bytes()).hexdigest() == layout_record['sha256']
        model = output / f'policy-{layout}/model'
        metadata = read(model / 'native-tools.json', {})
        if metadata:
            assert metadata['max_retries'] == 0 and 'ablation' not in metadata
            assert {tool['name'] for tool in metadata['tools']} == expected
            if config.get('transport_retry'):
                assert metadata['external_transport_retry'] == config['transport_retry']
                assert metadata['retry_wrapper_sha256'] == launch['retry_wrapper_sha256']
        retry_log = model / 'api-retries.jsonl'
        retry_events = [json.loads(line) for line in retry_log.read_text().splitlines()] if retry_log.exists() else []
        recovered = {event['call'] for event in retry_events if event['event'] == 'recovered'}
        for event in retry_events:
            if event['event'] == 'retry':
                assert event['robot_commands_issued'] == 0
                assert event['call'] in recovered or entry['outcome'] == 'api_interruption'
                assert event['retry_after_seconds'] >= 60
                later = [row for row in retry_events if row['call'] == event['call']]
                assert all(row['request_sha256'] == event['request_sha256'] for row in later)
        requests = sorted(model.glob('*.request.json'))
        responses = sorted(model.glob('*.response.json'))
        for path in requests:
            request = read(path)
            question = json.loads(request['question'])
            assert set(request['tools']) == expected and 'memory' in question and 'ablation' not in question
            assert question['action_interface'] == 'joint' and 'joint_semantics' in question
        for path in responses:
            response = read(path)
            assert response['status'] == 'completed'
            assert {tool['name'] for tool in response['tools'] if tool['type'] == 'function'} == expected
            for tool in response['tools']:
                if tool['type'] == 'function':
                    assert 'memory' in tool['parameters']['properties'] and 'memory' in tool['parameters']['required']
        if len(requests) > 1:
            assert entry['history_replayed_requests'] > 0
        if entry['success']:
            assert native['success'] and native['native_terminal'] and native['valid_sample']
            assert result['policy_returncode'] == result['simulator_returncode'] == 0
        error = result.get('error', '')
        if 'TimeoutError: Policy episode timeout' in error:
            assert manifest['episode_timeout'] > 0
            entry['outcome'] = 'policy_timeout'
            entry['termination_reason'] = f'{manifest["episode_timeout"]}-second policy timeout; native final result unavailable'
        elif 'TimeoutError: Simulator startup exceeded' in error:
            entry['outcome'] = 'startup_timeout'
            entry['termination_reason'] = '600-second simulator startup timeout'
        elif entry['outcome'] == 'model_stop':
            rows = [json.loads(line) for line in (output / f'policy-{layout}/decisions.jsonl').read_text().splitlines()]
            stopped = [row['decision']['action'] for row in rows if row['decision']['action'].get('stop')]
            entry['termination_reason'] = stopped[-1].get('reason') if stopped else 'Model stop_episode'
        elif entry['outcome'] == 'model_call_budget':
            entry['termination_reason'] = f'{config["max_model_calls"]}-request model call budget exhausted; no policy wall-clock timeout'
        elif entry['outcome'] == 'api_interruption':
            failures = [read(path) for path in sorted(model.glob('*.failure.json'))]
            entry['termination_reason'] = '; '.join(
                f'{failure["type"]}' + (f' HTTP {failure["http_status"]}' if failure.get('http_status') else '')
                + ': ' + failure.get('message', '')[:240] for failure in failures)
        else:
            entry['termination_reason'] = error or entry['status']
        if entry.get('video'):
            subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', entry['video'],
                            '-f', 'null', '-'], check=True, stdout=subprocess.DEVNULL, timeout=60)
        assert not entry['audit_errors'], (task, entry['audit_errors'])
        entry['final_audit_passed'] = True
        audit.append(dict(task=task, layout=layout, requests=len(requests), completed_responses=len(responses),
                          full_tools=True, memory_schema_and_context=True,
                          history_replay=entry['history_replayed_requests'], video_decoded=bool(entry.get('video')),
                          termination=entry['outcome'], source_sha256=hashlib.sha256((tasks / f'{task}.py').read_bytes()).hexdigest()))
    save(batch / 'batch.json', manifest)
    write_report(batch, manifest)
    summary = read(batch / 'summary.json')
    summary['policy_timeouts'] = sum(e['outcome'] == 'policy_timeout' for e in manifest['episodes'])
    summary['model_stops'] = sum(e['outcome'] == 'model_stop' for e in manifest['episodes'])
    summary['final_audit_passed'] = True
    save(batch / 'summary.json', summary)
    report = (batch / 'RESULT.md').read_text()
    report += '\n## 最终核验与限制\n\n'
    report += f'全部 {len(audit)} 场结果已保存；{summary["policy_timeouts"]} 场策略超时、{summary["model_stops"]} 场模型主动停止。'
    if manifest['episode_timeout']:
        report += f'策略总时长限制为 {manifest["episode_timeout"]} 秒，可能早于复杂任务的原生动作预算耗尽。'
    else:
        report += '本次取消策略累计运行时间上限，保留原生步数和 400 次模型调用预算。'
    report += '提前中止的分数不能视为任务终局成绩。\n\n'
    report += '最终检查通过：固定源码与封存 Harness、任务布局、实际完整工具 schema、文字 memory、历史回放、临时凭据清理及所有已生成视频完整解码。详见 final-audit.json。\n\n'
    report += '| 任务 | 终止原因 |\n| --- | --- |\n'
    for e in manifest['episodes']:
        reason = str(e['termination_reason']).replace('\n', ' ').replace('|', '\\|')
        report += f'| {e["task"]} | {reason} |\n'
    (batch / 'RESULT.md').write_text(report)
    save(batch / 'final-audit.json', {'passed': True, 'episodes': audit,
        'requests': sum(e['requests'] for e in audit), 'completed_responses': sum(e['completed_responses'] for e in audit)})
    (batch / 'final-integrity-check.log').write_text('PASS: fixed sources, sealed release, per-task layout/configuration.\nPASS: complete tool schemas, memory and history replay; temporary credentials removed.\nPASS: exported videos fully decoded.\n')
    print(json.dumps({k: summary[k] for k in ['completed_attempts', 'native_successes', 'native_completed',
        'api_errors', 'policy_timeouts', 'model_stops']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
