"""Run a separately recorded API-tool ablation with the sealed joint executor."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import traceback
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / 'release/harness'


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, value):
    temporary = path.with_name(path.name + '.writing')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


@contextmanager
def gpu_lease(gpu):
    folder = ROOT.parent / '.runtime/gpu-locks'
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f'gpu-{gpu}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def serve_on_device(gpu, arguments):
    """Route Isaac rendering at launch; execute the original serve.py bytes."""
    import torch
    torch.cuda.init()
    import isaaclab.app
    original_launcher = isaaclab.app.AppLauncher

    class DeviceLauncher(original_launcher):
        def __init__(self, *args, **kwargs):
            kwargs['kit_args'] = (kwargs.get('kit_args', '') +
                f' --/renderer/activeGpu={gpu} --/renderer/multiGpu/enabled=false').strip()
            super().__init__(*args, **kwargs)

    isaaclab.app.AppLauncher = DeviceLauncher
    sys.path.insert(0, str(HARNESS / 'scripts'))
    sys.argv = [str(HARNESS / 'scripts/serve.py'), *arguments]
    try:
        runpy.run_path(sys.argv[0], run_name='__main__')
    finally:
        isaaclab.app.AppLauncher = original_launcher


def prepare_assets(source, output):
    # Relocate only robot YAML paths in a per-run overlay; original assets stay intact.
    import yaml
    target = output / 'runtime-assets'
    target.mkdir()
    records = []
    for entry in source.iterdir():
        if entry.name != 'Robots':
            (target / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    (target / 'Robots').mkdir()
    for robot in (source / 'Robots').iterdir():
        destination = target / 'Robots' / robot.name
        if not robot.is_dir():
            destination.symlink_to(robot)
            continue
        destination.mkdir()
        for entry in robot.iterdir():
            adapted = destination / entry.name
            if entry.name != 'curobo.yml':
                adapted.symlink_to(entry, target_is_directory=entry.is_dir())
                continue
            config = yaml.safe_load(entry.read_text())
            kinematics = config['robot_cfg']['kinematics']
            old = {name: kinematics.get(name) for name in ('asset_root_path', 'urdf_path')}
            path = kinematics['urdf_path']
            urdf = source / path.split('/Assets/', 1)[1] if '/Assets/' in path else robot / path
            if not urdf.is_file():
                raise FileNotFoundError(urdf)
            kinematics.update(asset_root_path=str(robot), urdf_path=str(urdf))
            adapted.write_text(yaml.safe_dump(config, sort_keys=False))
            records.append({'source': str(entry), 'adapted': str(adapted), 'before': old,
                            'after': {name: kinematics[name] for name in old}})
    save(output / 'asset-adaptations.json', records)


def stop(process):
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def run(args):
    from physevo_release import verify, source_check
    pins = verify()
    robodojo = ROOT / 'dependencies/RoboDojo'
    source_check(robodojo, pins)
    runtime = ROOT.parent / 'envs/RoboDojo-private/bin/python'
    assets = ROOT.parent / 'assets/modelscope-RoboDojo/Assets'
    settings = tomllib.loads(args.api_config.read_text())
    provider = settings['model_providers'][settings['model_provider']]
    if provider.get('wire_api') != 'responses':
        raise ValueError('Current endpoint must support Responses')
    key = read(args.api_auth).get('OPENAI_API_KEY', '')
    if re.fullmatch(r'sk-[A-Za-z0-9_-]+', key) is None:
        raise ValueError('Credential does not match the original Harness key format')
    layout = assets / f'Eval_Layout/RoboDojo/arx_x5/0/{args.task}_{args.seed}.json'
    if not layout.is_file():
        raise ValueError(f'Group 0 layout L{args.seed} is unavailable')
    inventory = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid,memory.used,utilization.gpu',
                                         '--format=csv,noheader,nounits'], text=True)
    gpu = next((line.split(',') for line in inventory.splitlines()
                if line.split(',')[0].strip() == str(args.gpu)), None)
    if gpu is None or int(gpu[2]) > 512 or int(gpu[3]) > 5:
        raise ValueError(f'Physical GPU {args.gpu} must be idle before launch')
    gpu_uuid = gpu[1].strip()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if shutil.disk_usage(output).free < 8 * 1024**3:
        raise RuntimeError('At least 8 GiB free output space is required')
    temporary_root = ROOT.parent / '.runtime/tmp'
    temporary_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime_temp = Path(tempfile.mkdtemp(prefix=f'gpu{args.gpu}-', dir=temporary_root))
    sys.path.insert(0, str(HARNESS))
    from egg_harness.storage import EpisodeStorage
    from egg_harness.astra_api import select_provider
    from egg_harness.freeze import digest
    episode, trace = output / f'seed-{args.seed}', output / f'policy-{args.seed}'
    started = time.monotonic()
    result = {'task': args.task, 'seed_index': args.seed, 'seed_group': 0,
              'gpu': args.gpu, 'mode': 'development',
              'success': False, 'status': 'starting', 'human_interventions': 0,
              'started_at': datetime.now(timezone.utc).isoformat(), 'rgb_memory_enabled': False}
    result['episode_timeout_seconds'] = args.episode_timeout or None
    sim = policy = None
    storage = EpisodeStorage(output)
    with tempfile.TemporaryDirectory(prefix='release-pickup-auth-') as private:
        credential = Path(private) / 'key.txt'
        descriptor = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(key + '\n')
        del key
        config = read(args.task_config)
        retry_policy = config.get('transport_retry')
        retry_wrapper = ROOT / 'scripts/tool_ablation_policy.py'
        from tool_ablation_policy import MODES
        ablation_mode = config['tool_ablation']['mode']
        if ablation_mode not in MODES:
            raise ValueError('Unknown tool ablation mode')
        if ablation_mode == 'control_only':
            memory_policy = dict(explicit_episode_memory=True, model_history_replay=True,
                                 rgb_memory_enabled=False)
            config.update(memory_policy)
            config['tool_ablation'].update(memory_policy)
        if retry_policy:
            from full_tool_retry_policy import RETRY_POLICY
            if retry_policy != RETRY_POLICY:
                raise ValueError('Unexpected transport retry settings')
        config.pop('credentials', None)
        config.update(robot_runtime=str(runtime), robodojo_root=str(robodojo),
                      assets=str(output / 'runtime-assets'),
                      object_assets=str(assets / 'Object/RoboDojo'),
                      memory=str(HARNESS / 'memory/skills.json'), skills_root=str(HARNESS / 'skills'),
                      robodojo_revision=pins['robodojo']['commit'],
                      robodojo_required_submodules={'XPolicyLab': pins['xpolicylab']['commit']},
                      astra_provider='zzy-astra', zzy_credentials=str(credential),
                      provider_base_urls={'zzy-astra': provider['base_url'].rstrip('/')},
                      action_interface='joint',
                      public_task_requirements=config['public_task_requirements'])
        select_provider(config)  # Format validation only; no request and no credential output.
        save(output / f'config-{args.seed}.json', config)
        source_files = digest(HARNESS)
        save(output / f'source-{args.seed}.json', {'source': str(HARNESS), 'files': source_files})
        save(output / 'layout-provenance.json', {'seed_group': 0, 'layout_index': args.seed,
             'path': str(layout), 'sha256': hashlib.sha256(layout.read_bytes()).hexdigest()})
        save(output / 'launch-record.json', {'task': args.task, 'source': str(HARNESS),
             'hostname': os.uname().nodename, 'gpu': args.gpu, 'gpu_uuid': gpu_uuid,
             'seed_group': 0, 'layout_indices': [args.seed],
             'model': config['model'], 'reasoning_effort': config['reasoning_effort'],
             'rgb_memory_enabled': False, 'original_sdk_max_retries': 0,
             'explicit_episode_memory': config.get('explicit_episode_memory', True),
             'model_history_replay': config.get('model_history_replay', True),
             'episode_timeout_seconds': args.episode_timeout or None,
             'external_transport_retry': retry_policy,
             'retry_wrapper_sha256': hashlib.sha256(retry_wrapper.read_bytes()).hexdigest() if retry_policy else None,
             'release_manifest_sha256': hashlib.sha256((ROOT / 'release/manifest.json').read_bytes()).hexdigest(),
             'launcher': str(Path(__file__).resolve()),
             'launcher_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'tool_ablation': config['tool_ablation'],
             'scope': 'Sealed serve and joint executor; external API-tool ablation and transport retry wrapper',
             'device_routing': {'cuda_visible_devices': gpu_uuid, 'renderer_active_gpu': args.gpu,
                                'renderer_multi_gpu': False},
             'runtime_temp': str(runtime_temp),
             'development_case': {'reason': args.development_case_reason,
                                  'unseen_evaluation': False}})
        environment = dict(os.environ, PYTHONPATH=str(HARNESS), PYTHONDONTWRITEBYTECODE='1',
                           PYTHONNOUSERSITE='1', PYTHONUNBUFFERED='1', RSI_STORAGE_RESERVE_DIR=str(output),
                           TMPDIR=str(runtime_temp))
        environment.pop('LD_PRELOAD', None)
        try:
            storage.allocate()
            subprocess.run([str(runtime), '-B', str(Path(__file__).resolve()), '--prepare-assets',
                            '--assets', str(assets), '--output', str(output)], check=True)
            command = [str(runtime), '-B', str(Path(__file__).resolve()),
                       '--simulator-gpu', str(args.gpu), '--', '--output', str(episode),
                       '--robodojo-root', str(robodojo), '--assets', config['assets'],
                       '--object-assets', config['object_assets'], '--task', args.task,
                       '--seed-index', str(args.seed), '--run-mode', 'development',
                       '--action-interface', 'joint', '--robodojo-revision', config['robodojo_revision'],
                       '--required-submodule', 'XPolicyLab=' + pins['xpolicylab']['commit']]
            sim_env = dict(environment, CUDA_VISIBLE_DEVICES=gpu_uuid, LIVESTREAM='0',
                           OMNI_KIT_ACCEPT_EULA='YES', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
            with (output / f'sim-{args.seed}.log').open('w') as log:
                sim = subprocess.Popen(command, cwd=HARNESS, env=sim_env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            print(f'[release] Loading original simulator on GPU {args.gpu}: ' + str(output), flush=True)
            last_report = time.monotonic()
            while not (episode / 'ready.json').exists():
                storage.check()
                if sim.poll() is not None or (episode / 'error.txt').exists():
                    raise RuntimeError('Simulator startup failed; see sim log')
                if time.monotonic() - started > 600:
                    raise TimeoutError('Simulator startup exceeded 600 seconds')
                if time.monotonic() - last_report >= 15:
                    print(f'[release] Loading simulator, elapsed={int(time.monotonic()-started)}s', flush=True)
                    last_report = time.monotonic()
                time.sleep(1)
            with (output / f'policy-{args.seed}.log').open('w') as log:
                policy_command = ([sys.executable, '-B', str(retry_wrapper), '--harness', str(HARNESS), '--mode', ablation_mode]
                                  if retry_policy else [sys.executable, '-B', '-m', 'egg_harness.skill_policy'])
                policy = subprocess.Popen([*policy_command,
                    '--config', str(output / f'config-{args.seed}.json'), '--episode', str(episode),
                    '--trace', str(trace)], cwd=HARNESS, env=environment, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True)
            print('[release] Simulator ready; original policy started, no experience memory', flush=True)
            policy_started = time.monotonic()
            while policy.poll() is None:
                storage.check()
                if sim.poll() is not None:
                    raise RuntimeError('Simulator exited while policy was running')
                if args.episode_timeout > 0 and time.monotonic() - policy_started > args.episode_timeout:
                    raise TimeoutError('Policy episode timeout')
                if time.monotonic() - last_report >= 15:
                    observation = read(episode / 'latest.json', {})
                    calls = sum(1 for _ in (trace / 'model').glob('*.request.json'))
                    print(f'[release] step={observation.get("step",0)} model_calls={calls} elapsed={int(time.monotonic()-started)}s', flush=True)
                    last_report = time.monotonic()
                time.sleep(1)
            result['policy_returncode'] = policy.returncode
            if policy.returncode:
                raise RuntimeError('Original policy failed; see policy log')
            result['status'] = 'policy_finished'
        except BaseException:
            result.update(status='error_or_timeout', error=traceback.format_exc())
        finally:
            stop(policy)
            storage.release()
            if sim is not None and sim.poll() is None and (episode / 'commands').exists():
                finish = episode / 'commands/zz-runner-finish.json'
                temporary = finish.with_suffix('.tmp')
                save(temporary, {'id': 'zz-runner-finish', 'op': 'finish'})
                temporary.replace(finish)
                try:
                    sim.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    result['cleanup_timeout'] = True
            stop(sim)
            result['simulator_returncode'] = sim.returncode if sim is not None else None
            native = read(episode / 'runner-result.json', {})
            result['official_observation'] = native
            result['policy'] = read(trace / 'policy-result.json', {})
            if result['status'] == 'policy_finished':
                result['status'] = 'native_end' if native.get('native_terminal') else 'stopped_without_native_end'
                result['success'] = bool(native.get('native_terminal') and native.get('success')
                                         and native.get('valid_sample') and sim.returncode == 0)
            result['action_count'] = native.get('steps', 0)
            result['wall_seconds'] = time.monotonic() - started
            result['finished_at'] = datetime.now(timezone.utc).isoformat()
            try:
                verify()
                if digest(HARNESS) != source_files:
                    raise ValueError('Original Harness changed during execution')
                result['release_unchanged'] = True
            except Exception as error:
                result.update(release_unchanged=False, integrity_error=str(error), success=False)
            save(output / 'results.json', [result])
            from render_run import finalize_run
            finalize_run(output)
    print(json.dumps({'successes': int(result['success']), 'episodes': 1,
                      'status': result['status'], 'output': str(output)}), flush=True)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task')
    parser.add_argument('--task-config', type=Path)
    parser.add_argument('--seed', type=int, default=20, help='Layout index in group 0 (0..54)')
    parser.add_argument('--gpu', type=int, default=0, help='Idle physical CUDA/Vulkan GPU index')
    parser.add_argument('--development-case-reason', help='Required outside default development layouts 20..22')
    parser.add_argument('--episode-timeout', type=int, default=1800,
                        help='Policy wall-clock limit in seconds; 0 disables the limit')
    parser.add_argument('--api-config', type=Path, default=Path('/home/lanruya/.codex/config.toml'))
    parser.add_argument('--api-auth', type=Path, default=Path('/home/lanruya/.codex/auth.json'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--prepare-assets', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--assets', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--simulator-gpu', type=int, help=argparse.SUPPRESS)
    parser.add_argument('simulator_args', nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.simulator_gpu is not None:
        arguments = args.simulator_args
        if arguments[:1] == ['--']:
            arguments = arguments[1:]
        serve_on_device(args.simulator_gpu, arguments)
    elif args.prepare_assets:
        prepare_assets(args.assets.resolve(), args.output.resolve())
    else:
        if not args.task or not args.task_config:
            parser.error('--task and --task-config are required for execution')
        if not 0 <= args.seed <= 54 or args.gpu < 0 or args.episode_timeout < 0:
            parser.error('Use layout 0..54, a nonnegative GPU and timeout >= 0 (0 means unlimited)')
        if args.seed not in (20, 21, 22) and not (args.development_case_reason or '').strip():
            parser.error('Use --development-case-reason for layouts outside 20..22')
        if args.simulator_args:
            parser.error('Unexpected positional arguments')
        if args.output is None:
            stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
            args.output = ROOT / 'runs' / (args.task + '-full-tools-' + stamp)
        with gpu_lease(args.gpu):
            raise SystemExit(run(args))
