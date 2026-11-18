"""Audit and summarize a completed three-geometry-tool ablation batch."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    pending = path.with_name(path.name + '.writing')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    pending.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', type=Path)
    args = parser.parse_args()
    batch = args.batch.resolve()
    manifest = read(batch / 'batch.json')
    if manifest['status'] == 'running':
        raise ValueError('Batch is still running')
    baseline = Path(manifest['baseline'])
    expected = set(manifest['experiment']['remaining_tools'])
    disabled = set(manifest['experiment']['disabled_tools'])
    assert disabled == {'project_pixels', 'triangulate_point', 'triangulate_temporal_point'}
    assert expected == {'review_frames', 'verify_completion', 'stop_episode', 'control_arms'}
    for label, digest in manifest['baseline_result_sha256'].items():
        assert sha(baseline / label / 'results.json') == digest, label
    for path, digest in read(batch / 'offline-validation.json')['wrapper_sha256'].items():
        assert sha(Path(path)) == digest, path
    count = int(os.environ.get('GIT_CONFIG_COUNT', '0'))
    for checkout in (ROOT, ROOT / 'dependencies/RoboDojo', ROOT / 'dependencies/RoboDojo/XPolicyLab'):
        os.environ[f'GIT_CONFIG_KEY_{count}'] = 'safe.directory'
        os.environ[f'GIT_CONFIG_VALUE_{count}'] = str(checkout)
        count += 1
    os.environ['GIT_CONFIG_COUNT'] = str(count)
    from physevo_release import verify, source_check
    source_check(ROOT / 'dependencies/RoboDojo', verify())
    episodes = []
    for entry in manifest['episodes']:
        layout = entry['layout']
        output = batch / f'L{layout}'
        if not entry['results']:
            episodes.append(dict(layout=layout, outcome='launcher_failure',
                                 launcher_returncode=entry['launcher_returncode'], output=str(output)))
            continue
        result = read(output / 'results.json')[0]
        old_config = read(baseline / f'L{layout}/config-{layout}.json')
        config = read(output / f'config-{layout}.json')
        for value in (old_config, config):
            for key in ('assets', 'zzy_credentials', 'ablation'):
                value.pop(key)
        assert old_config == config, f'L{layout} changed non-ablation configuration'
        assert read(output / 'layout-provenance.json') == read(baseline / f'L{layout}/layout-provenance.json')
        assert read(output / f'source-{layout}.json') == read(baseline / f'L{layout}/source-{layout}.json')
        launch = read(output / 'launch-record.json')
        assert launch['ablation'] == manifest['experiment'] and launch['original_sdk_max_retries'] == 0
        for path, digest in launch['ablation_wrapper_sha256'].items():
            assert sha(Path(path)) == digest
        model = output / f'policy-{layout}/model'
        requests = sorted(model.glob('*.request.json'))
        responses = sorted(model.glob('*.response.json'))
        failures = [read(path) for path in sorted(model.glob('*.failure.json'))]
        tools, tokens = Counter(), Counter()
        for path in requests:
            request = read(path)
            question = json.loads(request['question'])
            assert set(request['tools']) == expected and request['replayed_call_id'] is None, path
            assert not any(key in question for key in ('memory', 'multiview_geometry', 'temporal_geometry')), path
            assert 'joint_semantics' in question, path
            assert question['joint_semantics']['preview_delta_rad'] == 0.05, path
            assert question['ablation'] == manifest['experiment'], path
        for path in responses:
            response = read(path)
            assert response['status'] == 'completed', path
            for tool in response['tools']:
                if tool['type'] == 'function':
                    assert tool['name'] in expected and 'memory' not in tool['parameters']['properties'], path
            for item in response['output']:
                if item['type'] == 'function_call':
                    assert item['name'] in expected and 'memory' not in json.loads(item['arguments']), path
                    tools[item['name']] += 1
            for key in ('input_tokens', 'output_tokens', 'total_tokens'):
                tokens[key] += (response.get('usage') or {}).get(key, 0)
        native = result['official_observation']
        if result['success']:
            assert native['native_terminal'] and native['valid_sample'] and native['score'] == 100
            assert result['policy_returncode'] == result['simulator_returncode'] == 0
        outcome = ('native_success' if result['success'] else 'api_interruption' if failures else
                   'model_stop' if tools['stop_episode'] else 'native_task_failure' if native.get('native_terminal')
                   else 'execution_interruption')
        video = output / 'rendered/rollout.mp4'
        media = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name,width,height:format=duration,size', '-of', 'json', str(video)], text=True))
        stream = media['streams'][0]
        assert (stream['codec_name'], stream['width'], stream['height']) == ('h264', 960, 480)
        save(output / 'video-validation.json', media)
        assert result['release_unchanged']
        assert not Path(read(output / f'config-{layout}.json')['zzy_credentials']).exists()
        episodes.append(dict(layout=layout, outcome=outcome, status=result['status'], success=result['success'],
            native_terminal=native['native_terminal'], valid_sample=native['valid_sample'], native_score=native['score'],
            steps=result['action_count'], model_calls=result['policy'].get('model_calls', 0), requests=len(requests),
            completed_responses=len(responses), api_errors=len(failures),
            api_error_statuses=[failure.get('http_status') for failure in failures], tool_counts=dict(tools),
            enabled_tools=manifest['experiment']['remaining_tools'], disabled_tools=sorted(disabled),
            memory_removed_from_context_and_schema=True, model_history_replayed=False,
            joint_motion_previews_preserved=True, non_ablation_config_matches_baseline=True,
            launcher_returncode=entry['launcher_returncode'], policy_returncode=result.get('policy_returncode'),
            simulator_returncode=result['simulator_returncode'], wall_seconds=result['wall_seconds'],
            release_unchanged=True, temporary_credential_removed=True, output=str(output), video=str(video),
            video_media=media, token_usage=dict(tokens), started_at=result['started_at'], finished_at=result['finished_at']))
    summary = dict(experiment=manifest['experiment'], baseline=str(baseline), status=manifest['status'],
        attempts=len(episodes), planned_layouts=manifest['layouts'], attempts_per_layout=1,
        native_completed=sum(e.get('native_terminal', False) for e in episodes),
        native_successes=sum(e.get('success', False) for e in episodes),
        api_interrupted=sum(e['outcome'] == 'api_interruption' for e in episodes),
        api_errors=sum(e.get('api_errors', 0) for e in episodes), episodes=episodes,
        updated_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
    save(batch / 'summary.json', summary)
    old_episodes = {e['layout']: e for e in read(baseline / 'summary.json')['episodes']}
    labels = {'native_success': '成功', 'api_interruption': 'API 中断', 'model_stop': '模型主动停止',
              'native_task_failure': '原生任务失败', 'execution_interruption': '执行中断', 'launcher_failure': '启动失败'}
    lines = ['# 关闭全部三个几何定位工具：实验结果', '',
        '日期：2026-10-13，lanruya 账号。L20/L21/L22 各尝试一次，GPU 0 串行执行。', '',
        f'**抓取成功 {summary["native_successes"]}/{len(episodes)}，原生终局 {summary["native_completed"]}/{len(episodes)}，API 错误 {summary["api_errors"]}。**', '',
        '| 布局 | 结果 | 分数 | 动作步数 | 请求 / 有效响应 / API 错误 | 策略 / 仿真退出码 | 视频 |',
        '| --- | --- | --- | --- | --- | --- | --- |']
    for e in episodes:
        if 'requests' not in e:
            lines.append(f'| L{e["layout"]} | 启动失败 | — | — | — | — | — |')
            continue
        score = f'{e["native_score"]:g}' if e['native_terminal'] else f'未到终局（中止观测 {e["native_score"]:g}）'
        lines.append(f'| L{e["layout"]} | {labels[e["outcome"]]} | {score} | {e["steps"]} | {e["requests"]} / {e["completed_responses"]} / {e["api_errors"]} | {e["policy_returncode"]} / {e["simulator_returncode"]} | [MP4]({e["video"]}) |')
    lines += ['', '## 实验范围', '',
        '关闭 project_pixels、triangulate_point、triangulate_temporal_point；从实际工具 schema 和本地允许调用映射同时移除，并移除对应系统操作说明和几何工具 context。保留 control_arms、review_frames、verify_completion、stop_episode。', '',
        '上一批的本回合文字 memory、模型历史回放和跨回合 RGB 经验 memory 继续关闭。保留技能、相机标定、三相机 RGB、当前机器人状态、关节运动预测（±0.05 弧度）、手臂姿态与指尖几何估计、前后画面、上一动作反馈、近期执行记录、动作执行与跟踪监测。', '',
        '相同模型 gpt-6-astra/high、joint 动作接口、1800 秒策略超时、300 秒请求超时、400 次模型调用预算、SDK max_retries=0。每布局仅一次，不自动重试。运行使用本人既有配置的同一 API 地址；临时凭据自动清理。', '',
        f'对照批次：[上一批报告]({baseline}/RESULT.md)。对照中的 L20 为 10 月 13 日一次 API 故障后重跑，L21/L22 为昨晚原结果；本次不覆盖这些结果。', '',
        '## 实际调用与对照', '',
        '| 布局 | 上一批步数 / 请求 | 本次步数 / 请求 | control_arms | review_frames | verify_completion | stop_episode | 耗时 |',
        '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for e in episodes:
        if 'requests' not in e:
            continue
        old = old_episodes[e['layout']]
        counts = [e['tool_counts'].get(tool, 0) for tool in ['control_arms', 'review_frames', 'verify_completion', 'stop_episode']]
        lines.append(f'| L{e["layout"]} | {old["steps"]} / {old["requests"]} | {e["steps"]} / {e["requests"]} | ' +
            ' | '.join(map(str, counts)) + f' | {e["wall_seconds"]:.2f} 秒 |')
    lines += ['', '三个关闭工具在本次所有响应中的实际调用数均为 0。耗时包含仿真启动与清理。API 中断独立于任务能力统计，未到原生终局的观测分数不作为完整任务最终成绩。', '',
        '每布局一次只提供初步证据。若成功，可说明这些轨迹在保留上述机器人反馈与技能辅助时，无须调用这三个显式几何定位工具也能完成；不能据此称为完全不依赖几何信息或估计稳定成功率。', '',
        '## 核验与文件', '',
        f'已核对 {sum(e.get("requests", 0) for e in episodes)} 份实际模型请求、{sum(e.get("completed_responses", 0) for e in episodes)} 份有效响应的工具列表、memory 和历史回放过滤，以及关节预测保留。每场非消融配置、布局和源代码均与对照匹配，原版 release/harness 不变，对照结果哈希不变。所有已产出视频通过 ffprobe 检查。', '',
        '结构化结果见 summary.json；运行参数及逐场退出状态见 experiment.json、batch.json、exit-codes.tsv；离线核验见 offline-validation.json。各场完整记录位于 L20/L21/L22 的 results.json、模型请求/响应、策略与仿真日志，以及 rendered/rollout.mp4。', '']
    (batch / 'RESULT.md').write_text('\n'.join(lines))
    (batch / 'final-integrity-check.log').write_text(
        'PASS: sealed release manifest, Harness source and pinned dependencies.\n'
        'PASS: non-ablation configurations/layouts match baseline; baseline results unchanged.\n'
        'PASS: actual tools/memory/history filters, joint previews, wrapper hashes and videos.\n')
    print(json.dumps({key: summary[key] for key in ['attempts', 'native_completed', 'native_successes', 'api_errors']}, ensure_ascii=False))
    print(batch / 'RESULT.md')


if __name__ == '__main__':
    main()
