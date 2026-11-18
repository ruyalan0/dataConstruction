"""Run native joint position goals without model-specified motion duration."""
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
from device_routing import launcher_options, simulator_environment

VARIANT = Path(__file__).resolve().parent
ROOT = VARIANT.parents[1]
HARNESS = VARIANT / 'harness'
sys.path.append(str(ROOT / 'scripts'))


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def resolve_interpolation_profile(task, requested=None, task_config=None):
    profile = requested if requested is not None else os.environ.get('ASTRA_INTERPOLATION_PROFILE')
    if profile is None:
        path = task_config if task_config is not None else VARIANT / 'task-configs' / (task + '.json')
        profile = read(path, {}).get('interpolation_profile', 'linear')
    if profile not in ('native', 'linear', 'quintic'):
        raise ValueError('Unknown interpolation profile: ' + str(profile))
    return profile


def save(path, value):
    temporary = path.with_name(path.name + '.writing')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


@contextmanager
def gpu_lease(gpu):
    folder = ROOT.parent / '.runtime/gpu-locks'
    folder.mkdir(parents=True, exist_ok=True)
    slot = int(os.environ.get('ASTRA_CAMPAIGN_GPU_SLOT', '0'))
    if slot not in (0, 1):
        raise ValueError('Only two comparison slots per GPU are allowed')
    lock_name = f'gpu-{gpu}.lock' if slot == 0 else f'gpu-{gpu}-slot-1.lock'
    with (folder / lock_name).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def serve_on_device(gpu, arguments):
    """Route Isaac rendering at launch and execute this variant's serve.py."""
    import torch
    if 'CUDA_VISIBLE_DEVICES' in os.environ:
        raise ValueError('Use ISAACSIM_GPU or --env-gpu without CUDA_VISIBLE_DEVICES')
    if int(os.environ.get('ISAACSIM_GPU', str(gpu))) != gpu:
        raise ValueError('ISAACSIM_GPU disagrees with the selected simulator GPU')
    torch.cuda.set_device(gpu)
    torch.cuda.init()
    import isaaclab.app
    original_launcher = isaaclab.app.AppLauncher

    class DeviceLauncher(original_launcher):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **launcher_options(gpu, kwargs))

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
    os.environ['ASTRA_INTERPOLATION_PROFILE'] = resolve_interpolation_profile(
        args.task, getattr(args, 'interpolation_profile', None), args.task_config)
    os.environ['ASTRA_CONTROLLER_TASK'] = args.task
    from physevo_release import source_check
    from verify_originals import verify as verify_originals, verify_release as verify
    if not verify_originals()['passed']:
        raise ValueError('Protected original source files changed')
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
    inventory = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid,memory.used,utilization.gpu,memory.free',
                                         '--format=csv,noheader,nounits'], text=True)
    gpu = next((line.split(',') for line in inventory.splitlines()
                if line.split(',')[0].strip() == str(args.gpu)), None)
    if gpu is None:
        raise ValueError(f'Physical GPU {args.gpu} is unavailable')
    if args.allow_shared_gpu and int(gpu[4]) < 12 * 1024:
        raise ValueError(f'Physical GPU {args.gpu} needs at least 12 GiB free for shared execution')
    if not args.allow_shared_gpu and (int(gpu[2]) > 512 or int(gpu[3]) > 5):
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
    from egg_harness.joint_goal import controller_settings
    from egg_harness.clocks import PHYSICS_HZ, clock_metadata
    from egg_harness.io_contract import precision_metadata
    from egg_harness.observation_pair import PROTOCOL
    from egg_harness.visual_history import visual_history_metadata
    from egg_harness.storage import EpisodeStorage
    from egg_harness.astra_api import select_provider
    from egg_harness.freeze import digest
    episode, trace = output / f'seed-{args.seed}', output / f'policy-{args.seed}'
    started = time.monotonic()
    result = {'task': args.task, 'seed_index': args.seed, 'seed_group': 0,
              'gpu': args.gpu, 'mode': 'development',
              'success': False, 'status': 'starting', 'human_interventions': 0,
              'started_at': datetime.now(timezone.utc).isoformat(), **visual_history_metadata()}
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
        if config.get('task') != args.task:
            raise ValueError('--task must match the selected task configuration')
        config.pop('action_hz', None)
        from egg_harness.auxiliary_observation import with_observer_skill
        config['skill_names'] = with_observer_skill(config['skill_names'])
        config.update(io_variant='astra_io_250hz_task_first_g_tube_order_20261117', scene_feedback_enabled=True, **clock_metadata(), model_output_duration=False,
                      goal_controller=controller_settings(args.task), privileged_scene_enabled=True,
                      **precision_metadata(), **visual_history_metadata(),
                      model_output_phase=False, history_deduplication=False,
                      input_protocol=PROTOCOL, memory_location='observation.memory')
        retry_policy = config.get('transport_retry')
        retry_wrapper = VARIANT / 'policy.py'
        MODES = {'control_only': {'control_arms'}}
        ablation_mode = config['tool_ablation']['mode']
        if ablation_mode not in MODES:
            raise ValueError('Unknown tool ablation mode')
        if ablation_mode == 'control_only':
            memory_policy = dict(explicit_episode_memory=True, model_history_replay=False,
                                 **visual_history_metadata())
            config.update(memory_policy)
            config['tool_ablation'].update(memory_policy)
        from full_tool_retry_policy import RETRY_POLICY
        if retry_policy != RETRY_POLICY:
            raise ValueError('This variant requires its recorded transport retry policy')
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
        save(output / 'launch-record.json', {'task': args.task, 'source': str(HARNESS), 'io_variant': 'astra_io_250hz_task_first_g_tube_order_20261117', 'scene_feedback_enabled': True,
             **clock_metadata(),
             'merge_provenance': read(VARIANT / 'merge-provenance.json'),
             'model_output_duration': False, 'privileged_scene_enabled': True, 'goal_controller': controller_settings(args.task), **precision_metadata(), 'model_output_phase': False,
             'hostname': os.uname().nodename, 'gpu': args.gpu, 'gpu_uuid': gpu_uuid,
             'gpu_preflight': {'allow_shared_gpu': args.allow_shared_gpu,
                               'memory_used_mib': int(gpu[2]), 'utilization_percent': int(gpu[3]),
                               'memory_free_mib': int(gpu[4])},
             'seed_group': 0, 'layout_indices': [args.seed],
             'model': config['model'], 'reasoning_effort': config['reasoning_effort'],
             **visual_history_metadata(), 'original_sdk_max_retries': 0,
             'input_protocol': PROTOCOL, 'memory_location': 'observation.memory',
             'explicit_episode_memory': config.get('explicit_episode_memory', True),
             'model_history_replay': config.get('model_history_replay', False),
             'episode_timeout_seconds': args.episode_timeout or None,
             'external_transport_retry': retry_policy,
             'retry_wrapper_sha256': hashlib.sha256(retry_wrapper.read_bytes()).hexdigest() if retry_policy else None,
             'release_manifest_sha256': hashlib.sha256((ROOT / 'release/manifest.json').read_bytes()).hexdigest(),
             'launcher': str(Path(__file__).resolve()),
             'launcher_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'tool_ablation': config['tool_ablation'],
             'scope': 'Native joint position goals without model duration; measured arrival, original 25 Hz task horizon and observation pairs',
             'device_routing': {'selection': 'ISAACSIM_GPU / --env-gpu', 'isaacsim_gpu': args.gpu,
                                'isaac_launcher_device': f'cuda:{args.gpu}', 'robodojo_device_id': args.gpu,
                                'physics_backend': 'original RoboDojo simulation configuration',
                                'renderer_active_gpu': args.gpu, 'renderer_multi_gpu': False,
                                'curobo_planner_device': f'cuda:{args.gpu}',
                                'cuda_visibility_remapped': False},
             'runtime_temp': str(runtime_temp),
             'development_case': {'reason': args.development_case_reason,
                                  'unseen_evaluation': False}})
        environment = dict(os.environ, PYTHONPATH=str(HARNESS), PYTHONDONTWRITEBYTECODE='1',
                           PYTHONNOUSERSITE='1', PYTHONUNBUFFERED='1', RSI_STORAGE_RESERVE_DIR=str(output),
                           TMPDIR=str(runtime_temp), ASTRA_CONTROLLER_TASK=args.task)
        environment.pop('LD_PRELOAD', None)
        environment.pop('CUDA_VISIBLE_DEVICES', None)
        try:
            storage.allocate()
            subprocess.run([str(runtime), '-B', str(Path(__file__).resolve()), '--prepare-assets',
                            '--assets', str(assets), '--output', str(output)], check=True)
            command = [str(runtime), '-B', str(Path(__file__).resolve()),
                       '--simulator-gpu', str(args.gpu), '--', '--output', str(episode),
                       '--robodojo-root', str(robodojo), '--assets', config['assets'],
                       '--object-assets', config['object_assets'], '--task', args.task,
                       '--seed-index', str(args.seed), '--run-mode', 'development',
                       '--action-interface', 'joint', '--physics-hz', str(PHYSICS_HZ), '--env-gpu', str(args.gpu),
                       '--robodojo-revision', config['robodojo_revision'],
                       '--required-submodule', 'XPolicyLab=' + pins['xpolicylab']['commit']]
            sim_env = simulator_environment(environment, args.gpu)
            with (output / f'sim-{args.seed}.log').open('w') as log:
                sim = subprocess.Popen(command, cwd=HARNESS, env=sim_env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            print(f'[joint-goal] Loading latched goals with {PHYSICS_HZ} Hz physics on GPU {args.gpu}: ' + str(output), flush=True)
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
                policy_command = [sys.executable, '-B', str(retry_wrapper),
                                  '--harness', str(HARNESS), '--mode', ablation_mode]
                policy = subprocess.Popen([*policy_command,
                    '--config', str(output / f'config-{args.seed}.json'), '--episode', str(episode),
                    '--trace', str(trace)], cwd=HARNESS, env=environment, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True)
            print('[joint-goal] Simulator ready; model submits goals after execution returns, with previous/current observations and explicit memory', flush=True)
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
                raise RuntimeError('Variant policy failed; see policy log')
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
                    raise ValueError('Variant Harness changed during execution')
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
    parser.add_argument('--task', default='general_pickup')
    parser.add_argument('--task-config', type=Path, default=VARIANT / 'general_pickup.json')
    parser.add_argument('--seed', type=int, default=20, help='Layout index in group 0 (0..54)')
    parser.add_argument('--env-gpu', '--gpu', dest='gpu', type=int,
                        default=int(os.environ.get('ISAACSIM_GPU', '0')),
                        help='Native Isaac GPU ordinal; defaults to ISAACSIM_GPU or 0; no CUDA visibility remapping')
    parser.add_argument('--allow-shared-gpu', action='store_true',
                        help='Allow a busy GPU with at least 12 GiB free; record its preflight occupancy')
    parser.add_argument('--gpu-slot', type=int, choices=[0, 1],
                        default=int(os.environ.get('ASTRA_CAMPAIGN_GPU_SLOT', '0')))
    parser.add_argument('--interpolation-profile', choices=['native', 'linear', 'quintic'],
                        default=None, help='Override environment or task configuration interpolation profile')
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
    args.interpolation_profile = resolve_interpolation_profile(args.task, args.interpolation_profile, args.task_config)
    os.environ['ASTRA_INTERPOLATION_PROFILE'] = args.interpolation_profile
    os.environ['ASTRA_CAMPAIGN_GPU_SLOT'] = str(args.gpu_slot)
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
            args.output = ROOT / 'runs' / (args.task + '-io-250hz-task-first-g-20261112-' + stamp)
        with gpu_lease(args.gpu):
            raise SystemExit(run(args))
