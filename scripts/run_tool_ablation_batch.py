"""Run one separately saved ten-task tool ablation with persistent API retry."""
import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from run_full_tool_task_batch import ROOT, read, save, summarize

MEMORY_POLICY = dict(explicit_episode_memory=True, model_history_replay=True,
                     rgb_memory_enabled=False)


def report(batch, manifest):
    episodes = manifest['episodes']
    labels = {'native_success': '成功', 'native_task_failure': '原生任务失败', 'model_stop': '主动停止',
              'api_interruption': '未恢复的 API 错误', 'model_call_budget': '决策预算耗尽',
              'launcher_failure': '启动失败', 'simulator_error': '仿真错误', 'execution_interruption': '执行中断'}
    summary = {key: manifest[key] for key in ('tasks', 'layouts', 'mode', 'enabled_tools', 'disabled_tools',
                                             'episode_timeout', 'model', 'reasoning_effort', 'status', 'transport_retry')}
    summary.update(completed_attempts=len(episodes), native_successes=sum(e['success'] for e in episodes),
                   native_completed=sum(e['native_terminal'] for e in episodes),
                   api_errors=sum(e['api_errors'] for e in episodes),
                   transport_retries=sum(e.get('transport_retries', 0) for e in episodes),
                   outcomes=dict(Counter(e['outcome'] for e in episodes)), episodes=episodes)
    summary.update({key: manifest.get(key, value) for key, value in MEMORY_POLICY.items()})
    save(batch / 'summary.json', summary)
    title = '全部 API 工具关闭：基础动作 JSON' if manifest['mode'] == 'all_tools_off' else '仅保留 control_arms'
    lines = [f'# {title}：10 项实验', '',
             f'状态 {manifest["status"]}，已结束 {len(episodes)}/10，原生成功 {summary["native_successes"]} 场。', '',
             '| 任务 | 结果 | 原生分数 | 步数 | 视频 |', '| --- | --- | --- | --- | --- |']
    for task in manifest['tasks']:
        while True:
            inventory = subprocess.check_output(['nvidia-smi', '--query-gpu=index,memory.used,utilization.gpu',
                                                '--format=csv,noheader,nounits'], text=True)
            gpu = next([value.strip() for value in line.split(',')] for line in inventory.splitlines()
                       if line.split(',')[0].strip() == '0')
            if int(gpu[1]) <= 512 and int(gpu[2]) <= 5:
                break
            manifest['waiting_for_gpu'] = 0
            save(batch / 'batch.json', manifest)
            time.sleep(20)
        manifest.pop('waiting_for_gpu', None)
        e = next((e for e in episodes if e['task'] == task), None)
        if not e:
            lines.append(f'| {task} | 待完成 | — | — | — |')
        else:
            score = str(e['native_score']) if e['native_terminal'] else f'中止观测 {e["native_score"]}'
            video = f'[MP4]({e["video"]})' if e.get('video') else '无'
            lines.append(f'| {task} | {labels[e["outcome"]]} | {score} | {e["steps"]} | {video} |')
    lines += ['', '## 配置', '', f'只记录本组 {manifest["mode"]} 的实验，独立目录与报告，不合并到全部工具组或另一消融组。', '',
              f'启用 API 工具：{manifest["enabled_tools"]}；关闭 API 工具：{manifest["disabled_tools"]}。', '',
              '全部工具关闭时，模型返回基础关节动作 JSON，由原版受限关节执行器执行；API tools=[]，tool_choice=none，不提供任何 function tool。仅保留 control_arms 时，只有这一种 function tool；通过其空臂 hold 动作推进观察，不提供终止检查或停止工具。', '',
              '三视角 RGB、公共机器人状态、关节语义及预测、当前/上一步反馈、回合文字记忆、前一轮历史和任务技能保留；跨回合 RGB 经验记忆关闭。review、投影、两种三角定位的能力提示从模型上下文移除。', '',
              'gpt-6-astra/high，group0 L20，每任务一次，无 make_kong；策略总时长不限，原生动作预算、400 次成功模型决策预算和单次请求 300 秒超时保持。API 连接错误、超时、408/409/429/5xx 无限退避重试同一请求，失败传输不重执行动作、不消耗决策预算。', '',
              '保留封存 Harness 和固定任务源码；外层包装器调整 API 工具和决策编码，原始关节执行器与物理约束保持。summary.json 是本组结构化结果，各任务 L20 目录含原始请求、响应、动作与视频。', '']
    (batch / 'RESULT.md').write_text('\n'.join(lines))


def audit(batch, manifest):
    from physevo_release import verify, source_check
    pins = verify()
    source_check(ROOT / 'dependencies/RoboDojo', pins)
    from tool_ablation_policy import MODES
    expected = MODES[manifest['mode']]
    rows = []
    for e in manifest['episodes']:
        o = Path(e['output'])
        assert e['release_unchanged'] and e['temporary_credential_removed'] and not e['audit_errors']
        metadata = read(o / 'policy-20/model/native-tools.json')
        assert {tool['name'] for tool in metadata['tools']} == expected
        assert metadata['tool_ablation']['mode'] == manifest['mode']
        if manifest['mode'] == 'control_only':
            assert 'memory' in metadata['tools'][0]['parameters']['required']
        requests = list((o / 'policy-20/model').glob('*.request.json'))
        for p in requests:
            request = read(p)
            assert set(request['tools']) == expected
            context = json.loads(request['question'])
            assert 'memory' in context and 'joint_semantics' in context
            assert not {'multiview_geometry', 'temporal_geometry', 'review_capability'} & context.keys()
        for p in (o / 'policy-20/model').glob('*.response.json'):
            response = read(p)
            assert response['status'] == 'completed'
            assert {tool['name'] for tool in response.get('tools', []) if tool['type'] == 'function'} == expected
            if not expected:
                assert not any(item['type'] == 'function_call' for item in response['output'])
        if e.get('video'):
            subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', e['video'], '-f', 'null', '-'],
                           check=True, stdout=subprocess.DEVNULL, timeout=60)
        rows.append(dict(task=e['task'], mode=manifest['mode'], tools=sorted(expected),
                         requests=len(requests), video_decoded=bool(e.get('video'))))
    assert len(rows) == 10
    save(batch / 'final-audit.json', dict(passed=True, mode=manifest['mode'], episodes=rows,
                                        separate_report=True, no_full_tool_merge=True))
    summary = read(batch / 'summary.json')
    summary['final_audit_passed'] = True
    save(batch / 'summary.json', summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', type=Path)
    args = parser.parse_args()
    batch = args.batch.resolve()
    manifest = read(batch / 'batch.json')
    if manifest['status'] != 'ready':
        raise ValueError('Only a prepared fresh ablation batch may start')
    if manifest['mode'] == 'control_only':
        manifest.update(MEMORY_POLICY)
        for task in manifest['tasks']:
            path = batch / f'configs/{task}.json'
            config = read(path)
            config.update(MEMORY_POLICY)
            config['tool_ablation'].update(MEMORY_POLICY)
            save(path, config)
    env = dict(os.environ, ISAACSIM_PORTABLE_ROOT=str(ROOT.parent / '.runtime/isaacsim'),
               PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    count = int(env.get('GIT_CONFIG_COUNT', '0'))
    for checkout in (ROOT, ROOT / 'dependencies/RoboDojo', ROOT / 'dependencies/RoboDojo/XPolicyLab'):
        env[f'GIT_CONFIG_KEY_{count}'] = 'safe.directory'
        env[f'GIT_CONFIG_VALUE_{count}'] = str(checkout)
        os.environ[f'GIT_CONFIG_KEY_{count}'] = 'safe.directory'
        os.environ[f'GIT_CONFIG_VALUE_{count}'] = str(checkout)
        count += 1
    env['GIT_CONFIG_COUNT'] = os.environ['GIT_CONFIG_COUNT'] = str(count)
    manifest.update(status='running', active=[])
    save(batch / 'batch.json', manifest)
    report(batch, manifest)
    for task in manifest['tasks']:
        o = batch / task / 'L20'
        o.parent.mkdir(exist_ok=True)
        manifest['active'] = [dict(task=task, layout=20, gpu=0, output=str(o))]
        save(batch / 'batch.json', manifest)
        command = [str(ROOT / '.venv-policy/bin/python'), '-u', '-B', str(ROOT / 'scripts/run_task_tool_ablation_local.py'),
                   '--task', task, '--task-config', str(batch / f'configs/{task}.json'), '--seed', '20', '--gpu', '0',
                   '--episode-timeout', '0', '--output', str(o)]
        print(f'START {manifest["mode"]} {task}', flush=True)
        with (batch / f'{task}-L20.launcher.log').open('w') as log:
            completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        e = summarize(o, task, 20, 0, completed.returncode, set(manifest['enabled_tools']))
        e['enabled_tools'] = e.pop('full_tools')
        decisions = o / 'policy-20/decisions.jsonl'
        if decisions.exists():
            decisions_data = [json.loads(line) for line in decisions.read_text().splitlines()]
            e['history_replayed_requests'] = sum(
                bool(read(p).get('history_replayed') or read(p).get('replayed_call_id'))
                for p in (o / 'policy-20/model').glob('*.request.json'))
            stopped = [row['decision']['action'] for row in decisions_data if row['decision']['action'].get('stop')]
            if stopped and not e['success'] and not e['api_errors']:
                e.update(outcome='model_stop', termination_reason=stopped[-1]['reason'])
        manifest['episodes'].append(e)
        manifest['active'] = []
        save(batch / 'batch.json', manifest)
        report(batch, manifest)
        print(f'END {task}: {e["outcome"]} score={e["native_score"]}', flush=True)
    manifest.update(status='complete', finished_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
    save(batch / 'batch.json', manifest)
    report(batch, manifest)
    audit(batch, manifest)
    print('COMPLETE ' + str(batch), flush=True)


if __name__ == '__main__':
    main()
