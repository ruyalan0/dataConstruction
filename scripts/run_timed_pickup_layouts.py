"""Run requested general_pickup layouts serially and export their timed Swift data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / 'variants/astra_io_250hz_timed'
EXPORTER = ROOT / 'scripts/export_timed_io_ms_swift.py'


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def episode_row(run, reused=False):
    result = read(run / 'results.json', [{}])[0]
    native = result.get('official_observation', {})
    return dict(layout=result.get('seed_index'), output=str(run), reused=reused,
                status=result.get('status', 'launch_failed'), success=result.get('success', False),
                score=native.get('score'), valid_sample=native.get('valid_sample', False),
                native_terminal=native.get('native_terminal', False), unstable=native.get('unstable'),
                control_ticks=native.get('steps'), task_steps=native.get('task_steps'),
                simulation_seconds=native.get('elapsed_simulation_seconds'),
                model_calls=result.get('policy', {}).get('model_calls'),
                invalid_actions=result.get('policy', {}).get('invalid_actions'),
                wall_seconds=result.get('wall_seconds'))


def command_log(command, path, environment):
    with path.open('x') as log:
        return subprocess.run(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                              stdout=log, stderr=subprocess.STDOUT).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--env-gpu', type=int, required=True)
    parser.add_argument('--layouts', type=int, nargs='+', default=[21, 22])
    parser.add_argument('--include-run', type=Path, action='append', default=[])
    args = parser.parse_args()
    if len(set(args.layouts)) != len(args.layouts) or not set(args.layouts) <= {20, 21, 22}:
        parser.error('Use unique development layouts from 20, 21, 22')
    batch = args.output.resolve()
    batch.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ, ISAACSIM_GPU=str(args.env_gpu), PYTHONDONTWRITEBYTECODE='1',
                       PYTHONUNBUFFERED='1', PYTHONNOUSERSITE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    environment.pop('CUDA_VISIBLE_DEVICES', None)
    for path in (ROOT, ROOT / 'dependencies/RoboDojo', ROOT / 'dependencies/RoboDojo/XPolicyLab'):
        index = int(environment.get('GIT_CONFIG_COUNT', '0'))
        environment.update(GIT_CONFIG_COUNT=str(index + 1), **{
            f'GIT_CONFIG_KEY_{index}': 'safe.directory', f'GIT_CONFIG_VALUE_{index}': str(path.resolve())})
    snapshot = batch / 'source-snapshot'
    snapshot.mkdir()
    hashes = {}
    for path in [Path(__file__), *[VARIANT / name for name in
                 ('run_local.py', 'policy.py', 'device_routing.py', 'general_pickup.json')]]:
        shutil.copyfile(path, snapshot / path.name)
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    save(batch / 'source-sha256.json', hashes)
    save(batch / 'resource-preflight.json', dict(at=now(), hostname=os.uname().nodename,
        gpus=subprocess.check_output(['nvidia-smi', '--query-gpu=index,name,memory.used,memory.free,utilization.gpu',
                                      '--format=csv,noheader'], text=True),
        output_free_bytes=shutil.disk_usage(batch).free, cgroup=Path('/proc/self/cgroup').read_text()))
    state = dict(task='general_pickup', variant='astra_io_250hz_timed', action_hz=250, observation_hz=25,
                 physics_hz=250, timing_protocol='timed_joint_v1', gpu=args.env_gpu, allow_shared_gpu=True,
                 concurrency=1, model='gpt-6-astra', reasoning_effort='high', enabled_tools=['control_arms'],
                 requested_layouts=args.layouts, status='running', started_at=now(), pid=os.getpid(),
                 active=None, episodes=[episode_row(run.resolve(), True) for run in args.include_run])
    if set(args.layouts) & {row['layout'] for row in state['episodes']}:
        raise ValueError('Included runs must not overlap newly requested layouts')
    save(batch / 'batch.json', state)
    try:
        for layout in args.layouts:
            output = batch / 'general_pickup' / f'L{layout}'
            command = [sys.executable, '-u', '-B', str(VARIANT / 'run_local.py'), '--task', 'general_pickup',
                       '--seed', str(layout), '--env-gpu', str(args.env_gpu), '--allow-shared-gpu',
                       '--output', str(output)]
            state['active'] = dict(layout=layout, output=str(output), command=command, started_at=now())
            save(batch / 'batch.json', state)
            print(f'START L{layout} {now()}', flush=True)
            code = command_log(command, batch / f'L{layout}-runner.log', environment)
            row = episode_row(output)
            row['runner_returncode'] = code
            row['export_returncode'] = None
            if row['status'] == 'native_end' and row['valid_sample'] and not row['unstable']:
                row['export_returncode'] = command_log([
                    sys.executable, '-B', str(EXPORTER), '--run', str(output),
                    '--output', str(output / 'swift-dataset')], batch / f'L{layout}-export.log', environment)
            row['export_passed'] = row['export_returncode'] == 0
            row['finished_at'] = now()
            state['episodes'].append(row)
            state['active'] = None
            save(batch / 'batch.json', state)
            print('FINISH ' + json.dumps(row, ensure_ascii=False), flush=True)
        valid = [row for row in state['episodes']
                 if row['status'] == 'native_end' and row['valid_sample'] and not row['unstable']]
        state['valid_samples'] = len(valid)
        state['successes'] = sum(row['success'] for row in valid)
        state['mean_score'] = sum(row['score'] for row in valid) / len(valid) if valid else None
        state['combined_export_returncode'] = None
        if len(valid) == len(state['episodes']):
            command = [sys.executable, '-B', str(EXPORTER)]
            for row in state['episodes']:
                command.extend(['--run', row['output']])
            command.extend(['--output', str(batch / 'swift-dataset')])
            state['combined_export_returncode'] = command_log(command, batch / 'combined-export.log', environment)
        state['status'] = 'completed' if (state['combined_export_returncode'] == 0
            and all(row.get('export_passed', True) for row in state['episodes'])) else 'completed_with_errors'
    except BaseException:
        import traceback
        state.update(status='error', error=traceback.format_exc())
        raise
    finally:
        state['finished_at'] = now()
        save(batch / 'batch.json', state)
        save(batch / 'summary.json', state)
        lines = ['# general_pickup：250 Hz 定时控制，L20/L21/L22', '',
                 'L20 引用已完成回合；L21、L22 各新跑一场，串行执行。',
                 '250 Hz 关节控制，25 Hz 任务/观测，原生预算 8 秒；Astra gpt-6-astra / high。', '',
                 '| Layout | 结果 | 分数 | 控制周期 | 仿真秒 | 模型调用 | 数据 |',
                 '|---|---|---:|---:|---:|---:|---|']
        for row in state['episodes']:
            label = '成功' if row['success'] else ('未成功' if row['valid_sample'] else '运行错误')
            lines.append(f"| L{row['layout']} | {label} | {row['score']} | {row['control_ticks']} | "
                         f"{row['simulation_seconds']} | {row['model_calls']} | "
                         f"[原始结果]({row['output']}/results.json) / [Swift]({row['output']}/swift-dataset/README.md) |")
        lines += ['', f"状态：{state['status']}；成功 {state.get('successes')}；均分 {state.get('mean_score')}。", '',
                  '合并训练数据：[swift-dataset/train.jsonl](swift-dataset/train.jsonl)；'
                  '[说明与核验](swift-dataset/README.md)。', '']
        (batch / 'SUMMARY.md').write_text('\n'.join(lines))
        print('BATCH ' + state['status'], flush=True)
    return 0 if state['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
