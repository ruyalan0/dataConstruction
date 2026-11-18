"""Execute prepared full-tool task episodes and persist an auditable live report."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, value):
    pending = path.with_name(path.name + '.writing')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    pending.replace(path)


def summarize(output, task, layout, gpu, code, expected):
    result = read(output / 'results.json', [{}])[0]
    native = result.get('official_observation', {})
    model = output / f'policy-{layout}/model'
    requests = sorted(model.glob('*.request.json'))
    responses = sorted(model.glob('*.response.json'))
    failures = [read(path) for path in sorted(model.glob('*.failure.json'))]
    retry_log = model / 'api-retries.jsonl'
    retry_events = [json.loads(line) for line in retry_log.read_text().splitlines()] if retry_log.exists() else []
    policy_log = output / f'policy-{layout}.log'
    policy_error = policy_log.read_text() if policy_log.exists() else ''
    counts, tokens = Counter(), Counter()
    replayed = memory_contexts = 0
    audit_errors = []
    for path in requests:
        request = read(path)
        context = json.loads(request['question'])
        if set(request['tools']) != expected:
            audit_errors.append(f'Unexpected tool set: {path}')
        if 'memory' in context:
            memory_contexts += 1
        if request.get('replayed_call_id') is not None:
            replayed += 1
    for path in responses:
        response = read(path)
        if response['status'] != 'completed':
            audit_errors.append(f'Incomplete response: {path}')
        for item in response['output']:
            if item['type'] == 'function_call':
                counts[item['name']] += 1
        for key in ('input_tokens', 'output_tokens', 'total_tokens'):
            tokens[key] += (response.get('usage') or {}).get(key, 0)
    success = bool(result.get('success'))
    if not result:
        outcome = 'launcher_failure'
    elif success:
        outcome = 'native_success'
    elif failures:
        outcome = 'api_interruption'
    elif native.get('native_terminal'):
        outcome = 'native_task_failure'
    elif counts['stop_episode']:
        outcome = 'model_stop'
    elif 'Model call budget exhausted' in policy_error:
        outcome = 'model_call_budget'
    elif (output / f'seed-{layout}/error.txt').exists():
        outcome = 'simulator_error'
    else:
        outcome = 'execution_interruption'
    video = output / 'rendered/rollout.mp4'
    media = None
    if video.exists():
        try:
            media = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=codec_name,width,height:format=duration,size', '-of', 'json', str(video)], text=True))
            stream = media['streams'][0]
            if (stream['codec_name'], stream['width'], stream['height']) != ('h264', 960, 480):
                audit_errors.append('Unexpected video codec or dimensions')
            save(output / 'video-validation.json', media)
        except Exception as error:
            audit_errors.append(f'Video check: {error}')
    elif requests:
        audit_errors.append('No exported video despite model requests')
    config = read(output / f'config-{layout}.json', {})
    credentials_removed = not Path(config.get('zzy_credentials', '/nonexistent')).exists()
    return dict(task=task, layout=layout, gpu=gpu, outcome=outcome, success=success,
        status=result.get('status', 'launcher_failure'), native_terminal=native.get('native_terminal', False),
        valid_sample=native.get('valid_sample', False), native_score=native.get('score'),
        steps=result.get('action_count', 0), requests=len(requests), completed_responses=len(responses),
        api_errors=len(failures), api_error_statuses=[f.get('http_status') for f in failures],
        tool_counts=dict(counts), full_tools=sorted(expected), memory_context_requests=memory_contexts,
        history_replayed_requests=replayed, token_usage=dict(tokens), launcher_returncode=code,
        launcher_sha256=read(output / 'launch-record.json', {}).get('launcher_sha256'),
        transport_retry=config.get('transport_retry'),
        transport_retries=sum(event['event'] == 'retry' for event in retry_events),
        recovered_api_calls=sum(event['event'] == 'recovered' for event in retry_events),
        policy_returncode=result.get('policy_returncode'), simulator_returncode=result.get('simulator_returncode'),
        wall_seconds=result.get('wall_seconds'), release_unchanged=result.get('release_unchanged', False),
        temporary_credential_removed=credentials_removed, audit_errors=audit_errors,
        started_at=result.get('started_at'), finished_at=result.get('finished_at'), output=str(output),
        video=str(video) if video.exists() else None, video_media=media)


def write_report(batch, manifest):
    episodes = manifest['episodes']
    planned = len(manifest['tasks']) * len(manifest['layouts'])
    summary = {key: manifest[key] for key in ('tasks', 'layouts', 'seed_group', 'model', 'reasoning_effort',
        'action_interface', 'disabled_tools', 'explicit_episode_memory', 'model_history_replay',
        'rgb_memory_enabled', 'original_sdk_max_retries', 'gpus', 'episode_timeout', 'endpoint', 'status')}
    summary.update(planned_episodes=planned, completed_attempts=len(episodes),
        native_successes=sum(e['success'] for e in episodes),
        native_completed=sum(e['native_terminal'] for e in episodes),
        api_errors=sum(e['api_errors'] for e in episodes),
        transport_retries=sum(e.get('transport_retries', 0) for e in episodes),
        recovered_api_calls=sum(e.get('recovered_api_calls', 0) for e in episodes),
        outcomes=dict(Counter(e['outcome'] for e in episodes)), episodes=episodes,
        updated_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
    save(batch / 'summary.json', summary)
    labels = {'native_success': '成功', 'native_task_failure': '原生任务失败', 'api_interruption': 'API 中断',
              'model_stop': '模型主动停止', 'simulator_error': '仿真错误',
              'launcher_failure': '启动失败', 'execution_interruption': '执行中断/超时',
              'policy_timeout': '策略超时（30 分钟）', 'startup_timeout': '仿真启动超时'}
    labels['model_call_budget'] = '模型调用预算耗尽'
    timeout_description = (f'{manifest["episode_timeout"]} 秒策略超时'
                           if manifest['episode_timeout'] else '策略总时长不设上限')
    lines = [f'# 完整工具：{len(manifest["tasks"])} 任务实验结果', '',
        f'状态：{manifest["status"]}。已结束 {len(episodes)}/{planned} 场，原生成功 {summary["native_successes"]} 场，原生终局 {summary["native_completed"]} 场，API 错误 {summary["api_errors"]}。', '',
        '| 任务 | 布局 | 结果 | 原生分数 | 步数 | 请求 / 响应 / API 错误 | 视频 |',
        '| --- | --- | --- | --- | --- | --- | --- |']
    for task in manifest['tasks']:
        for layout in manifest['layouts']:
            row = next((e for e in episodes if e['task'] == task and e['layout'] == layout), None)
            if row is None:
                lines.append(f'| {task} | L{layout} | 待完成 | — | — | — | — |')
                continue
            score = row['native_score']
            display = str(score) if row['native_terminal'] else f'中止观测 {score}' if score is not None else '—'
            video = f'[MP4]({row["video"]})' if row['video'] else '无（见启动/仿真日志）'
            lines.append(f'| {task} | L{layout} | {labels[row["outcome"]]} | {display} | {row["steps"]} | {row["requests"]} / {row["completed_responses"]} / {row["api_errors"]} | {video} |')
    lines += ['', '## 配置与统计口径', '',
        '使用封存 release/harness 原版策略，全部 7 个 joint 模式工具开启：control_arms、review_frames、project_pixels、triangulate_point、triangulate_temporal_point、verify_completion、stop_episode。没有安装工具过滤或消融策略。', '',
        '原版本回合文字 memory 和模型历史回放开启；新增跨回合 RGB 经验 memory 保持关闭。按封存 suite-task-selection.json 加载共同技能和对应任务技能。保留 RGB、相机标定、机器人状态、关节运动预测及执行反馈。', '',
        f'gpt-6-astra/high，group 0，布局 {manifest["layouts"]}，每任务/布局一次；joint 动作接口，300 秒请求超时、400 次模型调用预算、{timeout_description}，SDK max_retries=0。使用本人已有的同一 API 配置。GPU {manifest["gpus"]} 执行独立场次。', '',
        '仅 native_terminal=true、有效样本、原生 success=true 且仿真正常退出的场次判为成功。原生任务失败、API 中断、启动/仿真错误和执行超时分别记录；提前中止后的分数不代表完整任务成绩。无自动重跑或结果覆盖。', '',
        '每场记录保存在 <task>/L<layout>/：results.json、config-<layout>.json、launch-record.json、sim-<layout>.log、policy-<layout>.log、seed-<layout>/、policy-<layout>/model/ 和 rendered/rollout.mp4。仿真启动前失败可能没有画面或视频。', '',
        '结构化汇总：summary.json。进度与退出码：batch.json、exit-codes.tsv。任务文档、布局、技能和完整工具核验：preparation/。各场结果均保留，不修改此前抓取实验。', '']
    if any(e.get('transport_retry') for e in episodes) or manifest.get('transport_retry'):
        lines += ['## API 重试', '',
                  '启用重试的场次仅在 API 连接错误、请求超时、HTTP 408/409/429/5xx 后重发同一请求，按 60/120/240/300 秒退避并遵守 Retry-After，不限制重试次数。失败传输不消耗模型决策预算，不重复执行机器人动作，当前场景、工具、文字记忆和模型历史保持。SDK 内部 max_retries=0，重试由外层传输包装器实现。', '',
                  f'本次当前场次记录传输重试 {summary["transport_retries"]} 次、恢复请求 {summary["recovered_api_calls"]} 次；最终未恢复错误仍单独计入 API 中断。详细记录在各场 policy-20/model/api-retries.jsonl。', '']
    pending = batch / 'RESULT.md.writing'
    pending.write_text('\n'.join(lines))
    pending.replace(batch / 'RESULT.md')
    (batch / 'exit-codes.tsv').write_text('task\tlayout\tgpu\tlauncher_exit\n' + ''.join(
        f'{e["task"]}\t{e["layout"]}\t{e["gpu"]}\t{e["launcher_returncode"]}\n' for e in episodes))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', type=Path)
    args = parser.parse_args()
    batch = args.batch.resolve()
    manifest = read(batch / 'batch.json')
    if manifest['status'] != 'ready':
        raise ValueError('Only a fresh prepared batch can be launched')
    expected = set(manifest['full_tools'])
    jobs = [(task, layout) for task in manifest['tasks'] for layout in manifest['layouts']]
    lock = threading.Lock()
    manifest.update(status='running', active=[])

    def persist():
        save(batch / 'batch.json', manifest)
        write_report(batch, manifest)

    persist()
    def worker(gpu, assigned):
        environment = dict(os.environ, ISAACSIM_PORTABLE_ROOT=str(ROOT.parent / '.runtime/isaacsim'),
                           PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
        count = int(environment.get('GIT_CONFIG_COUNT', '0'))
        for checkout in (ROOT, ROOT / 'dependencies/RoboDojo', ROOT / 'dependencies/RoboDojo/XPolicyLab'):
            environment[f'GIT_CONFIG_KEY_{count}'] = 'safe.directory'
            environment[f'GIT_CONFIG_VALUE_{count}'] = str(checkout)
            count += 1
        environment['GIT_CONFIG_COUNT'] = str(count)
        for task, layout in assigned:
            output = batch / task / f'L{layout}'
            output.parent.mkdir(exist_ok=True)
            active = dict(task=task, layout=layout, gpu=gpu, output=str(output))
            with lock:
                manifest['active'].append(active)
                persist()
            print(f'START GPU{gpu} {task} L{layout}', flush=True)
            command = [str(ROOT / '.venv-policy/bin/python'), '-u', '-B',
                str(ROOT / 'scripts/run_release_task_local.py'), '--task', task,
                '--task-config', str(batch / f'configs/{task}.json'), '--seed', str(layout),
                '--gpu', str(gpu), '--episode-timeout', str(manifest['episode_timeout']),
                '--development-case-reason', 'User requested full-tool task experiment',
                '--api-config', '/home/lanruya/.codex/config.toml',
                '--api-auth', '/home/lanruya/.codex/auth.json', '--output', str(output)]
            with (batch / f'{task}-L{layout}.launcher.log').open('w') as log:
                completed = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
            entry = summarize(output, task, layout, gpu, completed.returncode, expected)
            with lock:
                manifest['episodes'].append(entry)
                manifest['active'].remove(active)
                persist()
            print(f'END {task} L{layout}: {entry["outcome"]}, score={entry["native_score"]}, steps={entry["steps"]}, requests={entry["requests"]}, api_errors={entry["api_errors"]}', flush=True)
    with ThreadPoolExecutor(max_workers=len(manifest['gpus'])) as pool:
        futures = [pool.submit(worker, gpu, jobs[index::len(manifest['gpus'])])
                   for index, gpu in enumerate(manifest['gpus'])]
        while any(not future.done() for future in futures):
            time.sleep(15)
            with lock:
                statuses = []
                for active in manifest['active']:
                    output = Path(active['output'])
                    layout = active['layout']
                    latest = read(output / f'seed-{layout}/latest.json', {})
                    calls = len(list((output / f'policy-{layout}/model').glob('*.request.json')))
                    statuses.append(f'{active["task"]}:step={latest.get("step", 0)},calls={calls}')
                print(f'PROGRESS {len(manifest["episodes"])}/{len(jobs)} ' + ' | '.join(statuses), flush=True)
        for future in futures:
            future.result()
    manifest.update(status='complete', finished_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
    persist()
    from physevo_release import verify
    verify()
    (batch / 'final-integrity-check.log').write_text('PASS: sealed release verified after batch; per-episode source and tool audits in summary.json.\n')
    subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/audit_full_tool_task_batch.py'), str(batch)], check=True)
    if manifest.get('merge_target'):
        subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/merge_api_task_rerun.py'),
                        str(manifest['merge_target']), str(batch)], check=True)
    print('COMPLETE ' + str(batch), flush=True)


if __name__ == '__main__':
    main()
