"""Worker-local independent episodes with incremental NAS/HDFS durability."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import traceback

from prepare_eight_eval import TASKS, save

HDFS = 'hdfs://storage.example.invalid/REDACTED'
STARTUPS = threading.Semaphore(4)
BACKUPS = ThreadPoolExecutor(max_workers=4)


def now(): return datetime.now(timezone.utc).isoformat()


def check(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def hdfs_archive(archive, uri, receipt):
    with archive.open('rb') as stream:
        expected = hashlib.file_digest(stream, 'sha256').hexdigest()
    with receipt.with_suffix('.log').open('w') as log:
        check(['hdfs', 'dfs', '-put', str(archive), uri], stdout=log, stderr=subprocess.STDOUT)
        process = subprocess.Popen(['hdfs', 'dfs', '-cat', uri], stdout=subprocess.PIPE, stderr=log)
        actual = hashlib.file_digest(process.stdout, 'sha256').hexdigest()
        assert process.wait() == 0, 'HDFS readback failed'
    assert actual == expected, 'HDFS readback hash mismatch'
    save(receipt, {'uri': uri, 'sha256': expected, 'bytes': archive.stat().st_size,
                   'verified_readback': True, 'at': now()})


class DurableCase:
    def __init__(self, case, destination):
        self.case, self.destination = case, destination
        self.known, self.shard = {}, 0

    def snapshot(self, final=False):
        archive = self.case.parent/(self.case.name+f'-shard-{self.shard:04d}.tar.gz')
        changed = {}
        with tarfile.open(archive, 'w:gz', compresslevel=1) as target:
            for path in sorted(self.case.rglob('*')):
                if not path.is_file() or path.name == 'cleanup-reserve.bin' or path.suffix == '.tmp': continue
                before = path.stat()
                if not final and time.time()-before.st_mtime < 2: continue
                relative = str(path.relative_to(self.case))
                fingerprint = [before.st_size, before.st_mtime_ns]
                if self.known.get(relative) == fingerprint: continue
                data = path.read_bytes()
                after = path.stat()
                if [after.st_size, after.st_mtime_ns] != fingerprint:
                    if final: raise RuntimeError('Completed case is still changing: '+relative)
                    continue
                info = tarfile.TarInfo(self.case.name+'/'+relative)
                info.size = len(data); info.mtime = before.st_mtime; info.mode = 0o644
                target.addfile(info, io.BytesIO(data))
                changed[relative] = fingerprint
        if not changed:
            archive.unlink()
            return
        receipt = self.destination/f'shard-{self.shard:04d}.json'
        hdfs_archive(archive, HDFS+'/'+archive.name, receipt)
        self.known.update(changed)
        save(self.destination/'durability.json', {'shards': self.shard+1,
             'files': len(self.known), 'last_verified_at': now(), 'final': final,
             'restoration': 'Extract shard archives in numeric order; later versions replace earlier files.'})
        self.shard += 1
        archive.unlink()  # Only this temporary upload copy; original episode remains on worker.

    def mirror(self):
        for name in ['launch.json', 'config.json', 'result.json', 'episode/latest.json',
                     'episode/runner-result.json', 'episode/exit-status.json', 'episode/error.txt',
                     'policy/error.txt', 'policy/model/transport-retries.jsonl',
                     'policy/model/native-tools.json', 'policy/model/transport-policy.json']:
            path = self.case/name
            if path.exists():
                destination = self.destination/name
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(destination.suffix+'.tmp')
                shutil.copyfile(path, temporary); temporary.replace(destination)
        latest = self.case/'episode/latest.json'
        if latest.exists():
            obs = json.loads(latest.read_text())
            for camera, filename in obs['cameras'].items():
                destination = self.destination/'preview'/f'{camera}.jpg'
                destination.parent.mkdir(exist_ok=True)
                temporary = destination.with_suffix('.tmp')
                shutil.copyfile(filename, temporary); temporary.replace(destination)
            save(self.destination/'status.json', {'at': now(), 'step': obs['step'],
                 'ended': obs['ended'], 'budget': obs['budget'],
                 'model_calls': len(list((self.case/'policy/model').glob('*.request.json'))),
                 'worker_case': str(self.case)})
        for name in ['simulator.log', 'policy.log']:
            path = self.case/name
            if path.exists():
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size-32000))
                    (self.destination/(name+'.tail')).write_bytes(stream.read())


def run_case(args, task, index, gpu, *, campaign=None, attempt=1, skill_revision=None,
             baseline=None, harness_commit='5e172339220b5a63b4d83a7c1b6220f9ff7f99d0', provider=None):
    baseline = args.root/'artifacts/baseline-5e17233' if baseline is None else baseline
    output = args.root/'artifacts/eight-task-eval' if campaign is None else campaign
    name = f'{task}-L{index}-attempt{attempt}'
    case = args.scratch/'runs'/name
    case.mkdir(parents=True, exist_ok=False)
    destination = output/'cases'/name
    destination.mkdir(parents=True, exist_ok=False)
    durable = DurableCase(case, destination)
    config = json.loads((output/'configs'/f'{task}.json').read_text())
    # Balance each task over the same two existing gateways, without failover.
    if provider is not None:
        assert provider in ('azure-astra', 'zzz-astra', 'zzy-astra') and config['astra_provider'] == provider, 'Explicit provider must match fixed campaign config'
    else:
        config['astra_provider'] = 'azure-astra' if index % 2 == 0 else 'zzz-astra'
    save(case/'config.json', config)
    save(case/'launch.json', {'task': task, 'layout_index': index, 'seed_group': 0,
        'harness_commit': harness_commit,
        'orchestration_commit': args.commit, 'baseline': str(baseline),
        'worker': os.environ['ARNOLD_WORKER_ID'], 'gpu': gpu,
        'started_at': now(), 'native_initial_state': True, 'mode': 'development_capability_evaluation',
        'action_interface': 'joint', 'provider': config['astra_provider'],
        'attempt': attempt, 'skill_revision': skill_revision,
        'episode_timeout_seconds': args.timeout})
    episode = case/'episode'
    env = dict(os.environ)
    env.pop('LD_PRELOAD', None)
    env.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
               RSI_STORAGE_RESERVE_DIR=str(case), PYTHONPATH=str(baseline)+':'+str(baseline/'scripts'))
    sys.path.insert(0, str(baseline))
    from egg_harness.storage import EpisodeStorage
    storage = EpisodeStorage(case)
    storage.allocate()
    sim = policy = pending = None
    result = {'task': task, 'layout_index': index, 'attempt': attempt,
              'skill_revision': skill_revision, 'started_at': now()}
    try:
        with STARTUPS:
            command = [sys.executable, str(baseline/'scripts/worker_driver.py'), '--record', str(case/'driver.json'), '--',
                'env', f'CUDA_VISIBLE_DEVICES={gpu}', 'OMNI_KIT_ACCEPT_EULA=YES', 'PYTHONNOUSERSITE=1',
                config['robot_runtime'], str(baseline/'scripts/serve.py'), '--output', str(episode),
                '--robodojo-root', config['robodojo_root'], '--robodojo-revision', config['robodojo_revision'],
                '--assets', config['assets'], '--object-assets', config['object_assets'],
                '--rtx-driver-version-workaround', '--seed-index', str(index), '--run-mode', 'development',
                '--task', task, '--action-interface', 'joint']
            for path, sha in config['robodojo_required_submodules'].items():
                command.extend(['--required-submodule', path+'='+sha])
            sim = subprocess.Popen(command, cwd=baseline, env=env, stdout=(case/'simulator.log').open('w'), stderr=subprocess.STDOUT)
            started = time.monotonic()
            while not (episode/'ready.json').exists():
                if sim.poll() is not None or (episode/'error.txt').exists(): raise RuntimeError('Simulator startup failed')
                if time.monotonic()-started > 600: raise TimeoutError('Simulator startup timeout')
                durable.mirror(); time.sleep(2)
        policy_command = [sys.executable, str(args.root/'scripts/eval_retry_policy.py'),
            '--baseline', str(baseline), '--expected-commit', harness_commit,
            '--config', str(case/'config.json'),
            '--episode', str(episode), '--trace', str(case/'policy')]
        if provider is not None: policy_command.extend(['--require-provider', provider])
        policy = subprocess.Popen(policy_command, cwd=baseline, env=env,
            stdout=(case/'policy.log').open('w'), stderr=subprocess.STDOUT)
        policy_started = time.monotonic()
        last_backup = 0
        while policy.poll() is None:
            storage.check(); durable.mirror()
            if pending is not None and pending.done(): pending.result(); pending = None
            if pending is None and time.monotonic()-last_backup >= 120:
                pending = BACKUPS.submit(durable.snapshot)
                last_backup = time.monotonic()
            if sim.poll() is not None: raise RuntimeError('Simulator exited during policy')
            if time.monotonic()-policy_started > args.timeout: raise TimeoutError('Episode wall-time budget exhausted')
            time.sleep(5)
        result.update(policy_exit=policy.returncode,
                      status='policy_completed' if policy.returncode == 0 else 'policy_error')
    except BaseException:
        result.update(status='execution_error', error=traceback.format_exc())
    finally:
        if policy and policy.poll() is None:
            policy.terminate(); policy.wait(timeout=30)
        if sim and sim.poll() is None and (episode/'commands').exists():
            save(episode/'commands/zz-runner-finish.json', {'id': 'zz-runner-finish', 'op': 'finish'})
            try: sim.wait(timeout=60)
            except subprocess.TimeoutExpired:
                sim.terminate(); sim.wait(timeout=30); result['simulator_cleanup_timeout'] = True
        elif sim and sim.poll() is None:
            sim.terminate(); sim.wait(timeout=30)
        storage.release()
        if (episode/'runner-result.json').exists(): result['native'] = json.loads((episode/'runner-result.json').read_text())
        result['finished_at'] = now()
        result['model_calls'] = len(list((case/'policy/model').glob('*.request.json')))
        usage = [json.loads(path.read_text())['usage'] for path in (case/'policy/model').glob('*.cost.json')]
        result['total_tokens'] = sum(item.get('total_tokens', 0) for item in usage)
        save(case/'result.json', result); durable.mirror()
        if pending is not None: pending.result()
        durable.snapshot(final=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--indices', type=int, nargs='+', default=[20, 21])
    parser.add_argument('--timeout', type=int, default=10800)
    args = parser.parse_args()
    args.root = args.root.resolve(); args.scratch = args.scratch.resolve()
    args.commit = subprocess.check_output(['git', '-C', str(args.root), 'rev-parse', 'HEAD'], text=True).strip()
    assert not subprocess.check_output(['git', '-C', str(args.root), 'status', '--porcelain']).strip(), 'Commit orchestration before launching'
    assert os.environ.get('ARNOLD_WORKER_ID') == '0000000', 'Run on the preflighted worker'
    record = json.loads((args.root/'artifacts/eight-task-eval/preparation/ready.json').read_text())
    assert record['indices'] == args.indices
    assert shutil.disk_usage(args.scratch).free > 100*1024**3
    output = args.root/'artifacts/eight-task-eval'
    assert not (output/'campaign.json').exists(), 'Campaign already launched; do not duplicate'
    with (output/'preflight/worker.txt').open('w') as stream:
        check([sys.executable, str(args.root/'scripts/resources.py')], stdout=stream)
    local = json.loads((output/'preflight/local.json').read_text())
    worker = next(json.loads(line) for line in (output/'preflight/worker.txt').read_text().splitlines() if line.startswith('{'))
    assert worker['cpu'] > local['cpu'] and worker['memory_limit'] > local['memory_limit'] and worker['nas']
    inventory = subprocess.check_output(['nvidia-smi', '--query-gpu=index,memory.total,memory.used', '--format=csv,noheader,nounits'], text=True)
    (output/'preflight/gpus.csv').write_text(inventory)
    devices = [[int(value.strip()) for value in line.split(',')] for line in inventory.splitlines()]
    assert len(devices) == 8 and all(total >= 40000 and used < 1000 for _, total, used in devices), 'Expected eight idle L20 GPUs'
    check(['hdfs', 'dfs', '-mkdir', '-p', HDFS])
    source_archive = args.scratch/'source-inputs.tar.gz'
    with tarfile.open(source_archive, 'w:gz') as target:
        for name, path in [('baseline-5e17233', args.root/'artifacts/baseline-5e17233'),
                           ('configs', output/'configs'), ('preparation', output/'preparation'),
                           ('preflight', output/'preflight'),
                           ('Eval_Layout', args.scratch/'assets/Eval_Layout')]:
            target.add(path, arcname=name)
        for name in ['prepare_eight_eval.py', 'eval_retry_policy.py', 'run_eight_eval.py', 'check_eval_retry.py']:
            target.add(args.root/'scripts'/name, arcname='orchestration/'+name)
        target.add(args.root/'EIGHT-TASK-EVAL-PROGRESS.md', arcname='EIGHT-TASK-EVAL-PROGRESS.md')
    hdfs_archive(source_archive, HDFS+'/source-inputs.tar.gz', output/'source-inputs-receipt.json')
    save(output/'campaign.json', {'started_at': now(), 'tasks': TASKS, 'indices': args.indices,
         'worker': '0000000', 'hdfs_root': HDFS, 'orchestration_commit': args.commit,
         'concurrency': len(TASKS)*len(args.indices), 'max_simulator_startups': 4,
         'gpu_assignment': 'one task per GPU, two independent layouts per GPU',
         'provider_assignment': 'first layout Azure, second layout ZZZ; no provider failover',
         'source_commit': record['harness_commit'], 'pid': os.getpid()})
    results = []
    with ThreadPoolExecutor(max_workers=len(TASKS)*len(args.indices)) as pool:
        futures = {pool.submit(run_case, args, task, index, gpu): (task, index)
                   for gpu, task in enumerate(TASKS) for index in args.indices}
        for future in as_completed(futures):
            task, index = futures[future]
            try: result = future.result()
            except BaseException:
                result = {'task': task, 'layout_index': index, 'status': 'orchestration_or_durability_error', 'error': traceback.format_exc()}
            results.append(result)
            save(output/'results.json', results)
            print(json.dumps(result), flush=True)
    BACKUPS.shutdown(wait=True)
    save(output/'completed.json', {'at': now(), 'cases': len(results),
         'source_commit': record['harness_commit'], 'results': results})


if __name__ == '__main__': main()
