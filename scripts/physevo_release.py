"""Portable, explicit entry point for the validated random12 Harness release."""
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

from frozen_runtime import load_runtime
from release_resources import admit, resources
from seal_release import inventory

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'release'


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def module(path):
    spec = importlib.util.spec_from_file_location('_physevo_' + path.stem, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def verify():
    manifest = read(RELEASE / 'manifest.json')
    if inventory(ROOT) != manifest['files']:
        raise ValueError('Release file inventory/checksums differ from the manifest')
    frozen = module(RELEASE / 'harness/egg_harness/freeze.py').verify(RELEASE / 'harness')
    dependencies = read(RELEASE / 'dependencies.json')
    if frozen['commit'] != dependencies['harness_commit']:
        raise ValueError('Harness provenance differs from release pin')
    return dependencies


def source_check(destination, dependencies):
    return module(RELEASE / 'harness/scripts/runtime_source.py').verify_runtime_source(
        destination, dependencies['robodojo']['commit'],
        {'XPolicyLab': dependencies['xpolicylab']['commit']})


def restore_sources(args):
    dependencies = verify()
    target = args.destination.resolve()
    if target.exists():
        raise FileExistsError('Use a new source destination: ' + str(target))
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE='1')
    for name, path in [('robodojo', target), ('xpolicylab', target / 'XPolicyLab')]:
        pin = dependencies[name]
        bundle = RELEASE / pin['bundle']
        subprocess.run(['git', 'clone', '--no-checkout', str(bundle), str(path)], env=env, check=True)
        subprocess.run(['git', '-C', str(path), 'checkout', '--detach', pin['commit']], env=env, check=True)
    print(json.dumps(source_check(target, dependencies), indent=2))


def route_env(proxy):
    env = {k: v for k, v in os.environ.items() if k.lower() not in ('http_proxy', 'https_proxy', 'all_proxy')}
    if proxy:
        env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, http_proxy=proxy, https_proxy=proxy)
    return env


def private_settings(path):
    settings = read(path)
    if settings['schema_version'] != 1:
        raise ValueError('Unsupported private configuration schema')
    return settings


def require_hdfs_uri(uri):
    parsed = urlsplit(uri)
    if (parsed.scheme != 'hdfs' or not parsed.hostname or parsed.hostname.endswith('.invalid')
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or not parsed.path.startswith('/') or parsed.path == '/' or any(c.isspace() for c in uri)):
        raise ValueError('Configure an explicit HDFS URI with a host and non-root path')
    return uri


def located(record, settings):
    """Resolve a public content hash through an explicitly supplied private map."""
    uri = require_hdfs_uri(settings['locations'][record['sha256']])
    return {**record, 'uri': uri}


def choose_route(directory, proxy, probe):
    candidates = [('', 'direct')]
    if proxy:
        candidates.append((proxy, 'configured'))

    def measure(candidate):
        route, name = candidate
        target = directory / f'probe-{name}-{uuid.uuid4().hex}.tar.gz'
        start = time.monotonic()
        try:
            process = subprocess.run(['hdfs', 'dfs', '-get', probe['uri'], str(target)],
                                     env=route_env(route), capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            ok = False
        else:
            ok = process.returncode == 0 and sha(target) == probe['sha256']
        target.unlink(missing_ok=True)
        return {'name': name, 'proxy': route, 'ok': ok, 'seconds': time.monotonic() - start}

    with ThreadPoolExecutor(max_workers=2) as pool:
        samples = list(pool.map(measure, candidates))
    good = [s for s in samples if s['ok']]
    if not good:
        raise RuntimeError('HDFS route probes failed: ' + json.dumps(samples))
    winner = min(good, key=lambda row: row['seconds'])
    save(directory / 'download-route.json', {'samples': samples, 'winner': winner})
    return route_env(winner['proxy'])


def extract_asset_part(archive, destination, files):
    expected = {item['path']: item for item in files}
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        if len(members) != len(expected) or {m.name for m in members} != set(expected):
            raise ValueError('Asset archive inventory mismatch')
        for member in members:
            target = (destination / member.name).resolve()
            if not member.isfile() or not target.is_relative_to(destination.resolve()):
                raise ValueError('Invalid asset archive member')
            if member.size != expected[member.name]['bytes']:
                raise ValueError('Asset size mismatch')
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as src, target.open('wb') as dst:
                shutil.copyfileobj(src, dst)
            if sha(target) != expected[member.name]['sha256']:
                raise ValueError('Asset file checksum mismatch: ' + member.name)


def restore_parts(parts, workers, restore):
    queue = iter(enumerate(parts))
    pool = ThreadPoolExecutor(max_workers=workers)
    pending = set()
    try:
        for _ in range(workers):
            item = next(queue, None)
            if item is not None:
                pending.add(pool.submit(restore, item))
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            # Observe all completed results before dispatching any more work.
            for future in done:
                future.result()
            for _ in done:
                item = next(queue, None)
                if item is not None:
                    pending.add(pool.submit(restore, item))
    finally:
        # In-flight shards finish their verification/receipt; queued work is stopped.
        pool.shutdown(wait=True, cancel_futures=True)


def restore_assets(args):
    verify()
    settings = private_settings(args.private_config)
    manifest = read(RELEASE / 'manifests/random12-assets.json')
    parts = [located(part, settings) for part in manifest['parts']]
    probe = located(read(RELEASE / 'dependencies.json')['download_probe'], settings)
    admission = admit(args.local_resources, args.scratch)
    target = args.destination.resolve()
    if not target.is_relative_to(args.scratch.resolve()):
        raise ValueError('Assets must be restored under worker-local scratch')
    target.mkdir(parents=True, exist_ok=True)
    receipts = target / 'receipts'
    receipts.mkdir(exist_ok=True)
    save(receipts / 'resources.json', admission)
    env = choose_route(receipts, args.proxy, probe)

    def restore(item):
        index, part = item
        receipt = receipts / f'{index:03d}.json'
        if receipt.exists():
            if read(receipt)['sha256'] != part['sha256']:
                raise ValueError('Part receipt differs from manifest')
            for record in part['files']:
                if sha(target / record['path']) != record['sha256']:
                    raise ValueError('Previously restored asset changed')
            return
        archive = target / f'.part-{index}-{uuid.uuid4().hex}.tar.gz'
        subprocess.run(['hdfs', 'dfs', '-get', part['uri'], str(archive)], env=env, check=True)
        if archive.stat().st_size != part['bytes'] or sha(archive) != part['sha256']:
            raise ValueError('Asset shard checksum mismatch')
        extract_asset_part(archive, target, part['files'])
        save(receipt, {'sha256': part['sha256'], 'files': len(part['files'])})
        archive.unlink()

    restore_parts(parts, args.workers, restore)
    save(receipts / 'complete.json', {'files': manifest['files'], 'parts': len(manifest['parts'])})
    print('Restored and verified', manifest['files'], 'files')


def restore_runtime(args):
    verify()
    settings = private_settings(args.private_config)
    manifest = located(read(RELEASE / 'manifests/worker-runtime.json'), settings)
    probe = located(read(RELEASE / 'dependencies.json')['download_probe'], settings)
    report = admit(args.local_resources, args.scratch)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'admission.json', report)
    save(output / 'worker.json', {'Status': 'Running', 'WorkerType': 'NVIDIA-L20',
                                 'WorkerSid': 'wkr_' + report['worker_id'], 'GpuCount': len(report['gpus'])})
    env = choose_route(output, args.proxy, probe)
    # The unchanged runtime restoration helper invokes HDFS as a subprocess.
    for key in list(os.environ):
        if key.lower() in ('http_proxy', 'https_proxy', 'all_proxy'):
            del os.environ[key]
    os.environ.update(env)
    hashes = read(RELEASE / 'manifest.json')['files']
    expected = {name: hashes['release/worker/' + name] for name in ('worker_driver.py', 'restore_worker_runtime.py')}
    restorer = load_runtime(RELEASE / 'worker', expected)
    save(output / 'runtime-manifest.json', manifest)
    restorer.restore(output / 'runtime-manifest.json', output / 'worker.json',
                     args.local_resources.resolve(), output / 'restore')


def prepare(args):
    pins = verify()
    settings = private_settings(args.private_config)
    provider_urls = {'zzy-astra': settings['provider_base_urls']['zzy-astra']}
    parsed = urlsplit(provider_urls['zzy-astra'])
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.hostname.endswith('.invalid')
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError('Configure a real HTTPS zzy-astra endpoint without credentials')
    source_check(args.robodojo.resolve(), pins)
    template = RELEASE / 'templates/random12' / (args.task + '.json')
    config = read(template)
    if args.layout != 20:
        raise ValueError('The packaged, verified task/layout cohort is random12 L20')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    config.update(robot_runtime=str(args.sim_python.resolve()), robodojo_root=str(args.robodojo.resolve()),
                  assets=str(args.assets.resolve()), object_assets=str(args.assets.resolve() / 'Object/RoboDojo'),
                  memory=str(RELEASE / 'templates/random12/calibration.json'),
                  skills_root=str(RELEASE / 'templates/random12/skills'),
                  provider_base_urls=provider_urls,
                  zzy_credentials=str(args.credentials.resolve()))
    path = output / 'configs' / (args.task + '.json')
    save(path, config)
    plan = {'task': args.task, 'layout': 20, 'run_id': uuid.uuid4().hex,
            'config_sha256': sha(path), 'release_manifest_sha256': sha(RELEASE / 'manifest.json'),
            'harness_commit': pins['harness_commit'], 'provider': 'zzy-astra',
            'maximum_model_calls': 400, 'episode_timeout_seconds': 10800}
    save(output / 'plan.json', plan)
    print(str(output / 'plan.json'))


def valid_measurement(result, reason):
    native = result.get('native', {})
    return (result.get('status') in {'policy_completed', 'policy_error'}
            and reason in {'native_success', 'native_unsuccessful_end', 'astra_stopped',
                           'model_call_budget_exhausted', 'harness_joint_boundary'}
            and native.get('valid_sample') is True and not native.get('unstable', False))


def run(args):
    pins = verify()
    require_hdfs_uri(args.hdfs_root)
    report = admit(args.local_resources, args.scratch)
    gpu = next(g for g in report['gpus'] if int(g['index']) == args.gpu)
    if int(gpu['total_mib']) - int(gpu['used_mib']) < 16 * 1024:
        raise ValueError('Selected GPU needs 16 GiB available')
    campaign = args.campaign.resolve()
    plan = read(campaign / 'plan.json')
    cfg_path = campaign / 'configs' / (plan['task'] + '.json')
    if sha(cfg_path) != plan['config_sha256'] or sha(RELEASE / 'manifest.json') != plan['release_manifest_sha256']:
        raise ValueError('Prepared inputs changed')
    cfg = read(cfg_path)
    source_check(Path(cfg['robodojo_root']), pins)
    # Fail before starting a simulator if inputs or credentials are absent.
    for name in ('robot_runtime', 'zzy_credentials', 'memory'):
        if not Path(cfg[name]).is_file():
            raise FileNotFoundError(cfg[name])
    manifest = read(RELEASE / 'manifests/random12-assets.json')
    asset_root = Path(cfg['assets']).parent
    for part in manifest['parts']:
        for record in part['files']:
            if sha(asset_root / record['path']) != record['sha256']:
                raise ValueError('Assets differ from the validated cohort')
    lock_dir = args.lock_dir.resolve()
    lock_dir.mkdir(parents=True, exist_ok=True)
    os.environ['RSI_REQUEST_LOCK_DIR'] = str(lock_dir)
    sys.path.insert(0, str(RELEASE / 'evaluation/scripts'))
    import run_eight_eval as runner
    from provider_concurrency import zzy_request_slot
    from report_eight_eval import end_reason
    runner.HDFS = args.hdfs_root.rstrip('/') + '/' + plan['run_id']
    job = SimpleNamespace(root=RELEASE / 'evaluation', scratch=args.scratch.resolve() / plan['run_id'],
                          timeout=plan['episode_timeout_seconds'], commit=read(RELEASE / 'manifest.json')['source_experiment_commit'])
    save(campaign / 'preflight.json', report)
    gpu_lock = (lock_dir / f'worker-{report["worker_id"]}-gpu-{args.gpu}.lock').open('a+')
    try:
        fcntl.flock(gpu_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with zzy_request_slot(directory=lock_dir / 'episode-slots'):
            subprocess.run(['hdfs', 'dfs', '-mkdir', '-p', runner.HDFS], check=True)
            result = runner.run_case(job, plan['task'], 20, args.gpu, campaign=campaign,
                                     baseline=RELEASE / 'harness', harness_commit=pins['harness_commit'],
                                     skill_revision='packaged-random12-frozen', provider='zzy-astra')
        folder = campaign / 'cases' / f'{plan["task"]}-L20-attempt1'
        reason = end_reason(folder, result)
        native = result.get('native', {})
        valid = valid_measurement(result, reason)
        summary = {'reason': reason, 'valid_measurement': valid,
                   'score': native.get('score') if valid else None, 'hdfs_root': runner.HDFS}
        save(campaign / 'summary.json', summary)
        print(json.dumps(summary))
        if not valid:
            raise RuntimeError('Episode did not produce a valid measurement; inspect archived logs')
    except Exception as error:
        # Preserve an already classified invalid result; archive/mkdir exceptions
        # still receive a separate explicit unscored record before propagating.
        if not (campaign / 'summary.json').exists():
            save(campaign / 'summary.json', {'reason': 'infrastructure_or_execution_error',
                 'valid_measurement': False, 'score': None, 'hdfs_root': runner.HDFS,
                 'error_type': type(error).__name__, 'error': str(error)})
        raise
    finally:
        runner.BACKUPS.shutdown(wait=True)
        gpu_lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('verify')
    p = commands.add_parser('resources')
    p.add_argument('--output', type=Path, required=True)
    p = commands.add_parser('sources')
    p.add_argument('--destination', type=Path, required=True)
    for name in ('restore-assets', 'restore-runtime', 'run'):
        p = commands.add_parser(name)
        p.add_argument('--local-resources', type=Path, required=True)
        p.add_argument('--scratch', type=Path, required=True)
        if name == 'run':
            p.add_argument('--campaign', type=Path, required=True)
            p.add_argument('--gpu', type=int, required=True)
            p.add_argument('--lock-dir', type=Path, required=True)
            p.add_argument('--hdfs-root', required=True)
        else:
            p.add_argument('--private-config', type=Path, required=True)
            p.add_argument('--proxy', default='')
            p.add_argument('--destination' if name == 'restore-assets' else '--output', type=Path, required=True)
            if name == 'restore-assets':
                p.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    p = commands.add_parser('prepare')
    p.add_argument('--task', choices=sorted(p.stem for p in (RELEASE / 'templates/random12').glob('*_random.json')), required=True)
    p.add_argument('--layout', type=int, default=20)
    p.add_argument('--private-config', type=Path, required=True)
    for name in ('robodojo', 'assets', 'sim-python', 'credentials', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'verify':
        print(json.dumps({'status': 'passed', 'dependencies': verify()}, indent=2))
    elif args.command == 'resources':
        save(args.output, resources())
    else:
        {'sources': restore_sources, 'restore-assets': restore_assets,
         'restore-runtime': restore_runtime, 'prepare': prepare, 'run': run}[args.command](args)


if __name__ == '__main__':
    main()
