"""Run fresh L20/L21/L22 general_pickup episodes with only control_arms enabled."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

from full_tool_retry_policy import RETRY_POLICY
from run_full_tool_task_batch import ROOT, read, save, summarize
from tool_ablation_policy import ALL_TOOLS

MEMORY_POLICY = dict(explicit_episode_memory=True, model_history_replay=True,
                     rgb_memory_enabled=False)


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()


def report(batch, manifest):
    episodes = manifest['episodes']
    summary = {key: manifest[key] for key in (
        'tasks', 'layouts', 'mode', 'enabled_tools', 'disabled_tools', 'episode_timeout',
        'model', 'reasoning_effort', 'status', 'transport_retry')}
    summary.update(completed_attempts=len(episodes), native_successes=sum(e['success'] for e in episodes),
                   native_completed=sum(e['native_terminal'] for e in episodes),
                   api_errors=sum(e['api_errors'] for e in episodes),
                   outcomes=dict(Counter(e['outcome'] for e in episodes)), episodes=episodes)
    summary.update({key: manifest.get(key, value) for key, value in MEMORY_POLICY.items()})
    save(batch / 'summary.json', summary)
    lines = ['# general_pickup：严格仅开放 control_arms', '',
             f"状态：{manifest['status']}；已结束 {len(episodes)}/3，成功 {summary['native_successes']}/3。", '',
             '| 布局 | 结果 | 分数 | 实际动作步数 | 请求数 | 视频 |', '| --- | --- | --- | --- | --- | --- |']
    for layout in manifest['layouts']:
        e = next((e for e in episodes if e['layout'] == layout), None)
        lines.append(f'| L{layout} | 待完成 | — | — | — | — |' if e is None else
                     f"| L{layout} | {e['outcome']} | {e['native_score']} | {e['steps']} | {e['requests']} | "
                     + (f"[MP4]({e['video']}) |" if e.get('video') else '无 |'))
    lines.extend(['', '每个布局从初始状态独立运行一次，仅启用 control_arms；review_frames、verify_completion、',
                  'stop_episode、project_pixels、triangulate_point、triangulate_temporal_point 全部关闭。', '',
                  ('回合文字 memory、模型历史回放及历史动作账本关闭；只保留当前/上一步公共观测、'
                   '实际动作反馈、任务技能和机器人状态。跨回合 RGB 经验记忆关闭。'
                   if manifest.get('explicit_episode_memory', True) is False else
                   '保留原版回合文字 memory、前一轮模型/工具交互回放和近期动作账本；'
                   '跨回合 RGB 经验记忆关闭。'), '',
                  'gpt-6-astra/high，GPU 0 串行；原生动作预算和 400 次模型决策预算不变，',
                  '策略总时长不限，API 传输故障按原十任务批次规则重试同一请求；不自动重跑回合。', ''])
    (batch / 'RESULT.md').write_text('\n'.join(lines))


def audit_episode(output, layout, result):
    if not result['release_unchanged'] or not result['temporary_credential_removed'] or result['audit_errors']:
        raise ValueError(f'Run integrity failed for L{layout}: {result["audit_errors"]}')
    model = output / f'policy-{layout}/model'
    metadata = read(model / 'native-tools.json')
    assert {t['name'] for t in metadata['tools']} == {'control_arms'}
    assert metadata['enabled_tools'] == ['control_arms']
    assert metadata['tool_ablation']['mode'] == 'control_only'
    assert all(metadata.get(key, value) is value for key, value in MEMORY_POLICY.items())
    assert 'memory' in metadata['tools'][0]['parameters']['properties']
    assert 'memory' in metadata['tools'][0]['parameters']['required']
    requests, responses = sorted(model.glob('*.request.json')), sorted(model.glob('*.response.json'))
    assert requests and len(requests) == len(responses)
    calls = 0
    for index, p in enumerate(requests):
        request = read(p)
        assert request['tools'] == ['control_arms']
        context = json.loads(request['question'])
        assert isinstance(context['memory'], str)
        assert bool(request.get('replayed_call_id')) == (index > 0)
        assert not {'multiview_geometry', 'temporal_geometry', 'review_capability', 'reviewed_observations'} & context.keys()
    for p in responses:
        response = read(p)
        assert response['status'] == 'completed'
        assert {t['name'] for t in response.get('tools', []) if t['type'] == 'function'} == {'control_arms'}
        functions = [x for x in response['output'] if x['type'] == 'function_call']
        assert len(functions) == 1 and functions[0]['name'] == 'control_arms'
        assert isinstance(json.loads(functions[0]['arguments'])['memory'], str)
        calls += len(functions)
    assert result['native_terminal'] and result['valid_sample']
    if result.get('video'):
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', result['video'], '-f', 'null', '-'],
                       check=True, stdout=subprocess.DEVNULL, timeout=60)
    return dict(layout=layout, passed=True, enabled_tools=['control_arms'], requests=len(requests),
                completed_responses=len(responses), function_calls=calls)


def prepare(batch):
    # Use the tested ten-task control-only configuration, with pickup-specific skills/requirements.
    config = read(ROOT / 'runs/ten-tasks-control-only-20261014-001220/configs/arrange_largest_number.json')
    old = read(ROOT / 'runs/pickup-no-geometry-memory-20261013-132932/L20/config-20.json')
    config.update(task='general_pickup', skill_names=old['skill_names'],
                  public_task_requirements=old['public_task_requirements'])
    config.update(MEMORY_POLICY)
    config['tool_ablation'].update(MEMORY_POLICY)
    assert config['transport_retry'] == RETRY_POLICY
    assert config['tool_ablation']['enabled_tools'] == ['control_arms']
    assert set(config['tool_ablation']['disabled_tools']) == ALL_TOOLS - {'control_arms'}
    batch.mkdir(parents=True, exist_ok=False)
    (batch / 'configs').mkdir()
    save(batch / 'configs/general_pickup.json', config)
    manifest = dict(tasks=['general_pickup'], layouts=[20, 21, 22], seed_group=0,
                    mode='control_only', enabled_tools=['control_arms'], disabled_tools=sorted(ALL_TOOLS - {'control_arms'}),
                    model=config['model'], reasoning_effort=config['reasoning_effort'],
                    transport_retry=RETRY_POLICY, episode_timeout=0, episodes=[], active=[],
                    status='ready', created_at=now(), runner=str(Path(__file__).resolve()),
                    runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    manifest.update(MEMORY_POLICY)
    save(batch / 'batch.json', manifest)
    report(batch, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, required=True)
    args = parser.parse_args()
    batch = args.batch.resolve()
    manifest = prepare(batch)
    os.environ.update(ISAACSIM_PORTABLE_ROOT=str(ROOT.parent / '.runtime/isaacsim'),
                      PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    count = int(os.environ.get('GIT_CONFIG_COUNT', '0'))
    for checkout in (ROOT, ROOT / 'dependencies/RoboDojo', ROOT / 'dependencies/RoboDojo/XPolicyLab'):
        os.environ[f'GIT_CONFIG_KEY_{count}'] = 'safe.directory'
        os.environ[f'GIT_CONFIG_VALUE_{count}'] = str(checkout)
        count += 1
    os.environ['GIT_CONFIG_COUNT'] = str(count)
    from physevo_release import verify, source_check
    pins = verify()
    source_check(ROOT / 'dependencies/RoboDojo', pins)
    manifest.update(status='running', started_at=now())
    audits = []
    try:
        for layout in manifest['layouts']:
            output = batch / 'general_pickup' / f'L{layout}'
            manifest['active'] = [dict(task='general_pickup', layout=layout, gpu=0, output=str(output))]
            save(batch / 'batch.json', manifest)
            report(batch, manifest)
            print(f'START general_pickup L{layout} tools=[control_arms]', flush=True)
            with (batch / f'L{layout}.launcher.log').open('w') as stream:
                completed = subprocess.run([
                    sys.executable, '-u', '-B', str(ROOT / 'scripts/run_task_tool_ablation_local.py'),
                    '--task', 'general_pickup', '--task-config', str(batch / 'configs/general_pickup.json'),
                    '--seed', str(layout), '--gpu', '0', '--episode-timeout', '0', '--output', str(output)],
                    cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            result = summarize(output, 'general_pickup', layout, 0, completed.returncode, {'control_arms'})
            result['enabled_tools'] = result.pop('full_tools')
            manifest['episodes'].append(result)
            manifest['active'] = []
            save(batch / 'batch.json', manifest)
            report(batch, manifest)
            audits.append(audit_episode(output, layout, result))
            save(batch / 'episode-audits.json', audits)
            print(f'END L{layout}: {result["outcome"]} score={result["native_score"]} steps={result["steps"]}', flush=True)
        verify()
        source_check(ROOT / 'dependencies/RoboDojo', pins)
        assert len(audits) == 3
        manifest.update(status='complete', finished_at=now())
        save(batch / 'batch.json', manifest)
        report(batch, manifest)
        save(batch / 'final-audit.json', dict(passed=True, mode='control_only', episodes=audits))
        summary = read(batch / 'summary.json')
        summary['final_audit_passed'] = True
        save(batch / 'summary.json', summary)
        print('COMPLETE ' + str(batch), flush=True)
    except BaseException as error:
        manifest.update(status='error', error=f'{type(error).__name__}: {error}')
        save(batch / 'batch.json', manifest)
        report(batch, manifest)
        raise


if __name__ == '__main__':
    main()
