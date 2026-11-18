"""Restore a verified uv runtime on a fresh Running worker, using its local disk."""

import argparse
import hashlib
import json
import os
import ctypes
import tempfile
import shutil
import subprocess
import sys
import tarfile
import traceback
from datetime import UTC, datetime
from pathlib import Path
from worker_driver import prepare


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def restore(manifest_path, worker_record_path, local_resources_path, output):
    if (output / 'status.json').exists():
        raise FileExistsError('A restore attempt already exists; inspect it before starting a new attempt')
    output.mkdir(parents=True, exist_ok=True)
    def state(name, **values):
        save(output / 'status.json', {'state': name, 'at': datetime.now(UTC).isoformat(), **values})
    try:
        manifest = json.loads(manifest_path.read_text())
        worker = json.loads(worker_record_path.read_text())
        local = json.loads(local_resources_path.read_text())
        if worker['Status'] != 'Running' or worker['GpuCount'] not in (4, 8) or worker['WorkerType'] != 'NVIDIA-L20':
            raise ValueError('This restore requires a recorded Running 4/8-card L20 worker')
        if os.environ.get('ARNOLD_WORKER_ID') != worker['WorkerSid'].removeprefix('wkr_'):
            raise ValueError('Run restoration on the selected worker, not the local Pod')
        if sys.version_info[:2] != (3, 11):
            raise ValueError('The archived uv environment requires system Python 3.11')
        cgroup = Path('/sys/fs/cgroup')
        def read(name): return int((cgroup / name).read_text())
        driver_parent = Path(tempfile.mkdtemp(prefix="rsi-restore-driver-"))
        driver = prepare(Path("/proc/driver/nvidia/version").read_text(),
                         Path("/usr/lib/x86_64-linux-gnu"), driver_parent / "lib")
        for target in driver["libraries"].values():
            ctypes.CDLL(target)
        driver_version = driver["kernel_version"]
        save(output / "driver.json", driver)
        env = dict(os.environ, LD_LIBRARY_PATH="/usr/local/cuda-12.6/compat:" + driver["directory"],
                   OMNI_KIT_ACCEPT_EULA='YES')
        env.pop('LD_PRELOAD', None)
        inventory = subprocess.check_output(['nvidia-smi', '--query-gpu=index,name,memory.total,memory.free',
                                            '--format=csv,noheader,nounits'], env=env, text=True)
        gpus = [line.split(',') for line in inventory.splitlines()]
        resources = {'worker': worker, 'cpu_quota': read('cpu/cpu.cfs_quota_us') / read('cpu/cpu.cfs_period_us'),
                     'memory_limit_bytes': read('memory/memory.limit_in_bytes'),
                     'memory_used_bytes': read('memory/memory.usage_in_bytes'),
                     'memsw_limit_bytes': read('memory/memory.memsw.limit_in_bytes'),
                     'swap_devices': Path('/proc/swaps').read_text(), 'gpu_inventory': inventory,
                     'tmp_free_bytes': shutil.disk_usage('/tmp').free, 'nas_free_bytes': shutil.disk_usage(output).free,
                     'local_pod_comparison': local, 'nvidia_kernel_driver': driver_version,
                     'driver_library_path': env['LD_LIBRARY_PATH']}
        save(output / 'resources.json', resources)
        if (resources['cpu_quota'] <= local['cpu'] or
            resources['memory_limit_bytes'] <= local['memory_limit'] or
            resources['memory_limit_bytes'] - resources['memory_used_bytes'] < 32 * 1024**3 or
            resources['tmp_free_bytes'] < 80 * 1024**3 or len(gpus) != worker['GpuCount'] or
            any('L20' not in gpu[1] or int(gpu[3]) < 12 * 1024 for gpu in gpus)):
            raise RuntimeError('Worker cgroup/GPU/local-disk capacity does not satisfy the placement plan')
        roots = manifest['top_level_members']
        for name in roots:
            if not name or Path(name).name != name or (Path('/tmp') / name).exists():
                raise ValueError('Runtime destination must be a new top-level directory: ' + name)
        state('downloading', uri=manifest['uri'])
        archive = Path('/tmp') / ('robodojo-runtime-' + worker['WorkerSid'] + '.tar.gz')
        if archive.exists():
            raise FileExistsError(archive)
        subprocess.run(['hdfs', 'dfs', '-get', manifest['uri'], str(archive)], check=True)
        with archive.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if archive.stat().st_size != manifest['bytes'] or actual != manifest['sha256']:
            raise ValueError('Downloaded runtime checksum differs from the verified source archive')
        state('validating_archive', sha256=actual)
        count, seen = 0, set()
        with tarfile.open(archive, 'r|gz') as source:
            for member in source:
                path = Path(member.name)
                if path.is_absolute() or '..' in path.parts or path.parts[0] not in roots:
                    raise ValueError('Unexpected runtime archive member: ' + member.name)
                seen.add(path.parts[0])
                count += 1
        if count != manifest['members'] or seen != set(roots):
            raise ValueError('Runtime archive inventory mismatch')
        state('extracting', sha256=actual)
        subprocess.run(['tar', '-xzf', str(archive), '-C', '/tmp', '--no-same-owner'], check=True)
        command = ['/tmp/robodojo-uv/bin/python', '-c',
                   ('import json,torch,importlib.metadata as m; '
                   'assert torch.cuda.is_available(); '
                   'print(json.dumps({"torch":torch.__version__,"cuda":torch.version.cuda,'
                   '"gpu_count":torch.cuda.device_count(),"isaacsim":m.version("isaacsim")}))')]
        runtime = json.loads(subprocess.check_output(command, env=env, text=True))
        if runtime['gpu_count'] != worker['GpuCount']:
            raise ValueError('CUDA device count differs from worker allocation')
        if runtime['isaacsim'] != manifest['runtime_versions']['packages']['isaacsim']:
            raise ValueError('Unexpected Isaac Sim version')
        save(output / 'runtime.json', runtime)
        state('ready', worker_id=worker['WorkerSid'], runtime=runtime, sha256=actual)
    except Exception:
        error = traceback.format_exc()
        (output / 'error.txt').write_text(error)
        state('failed', error=error)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--worker-record', type=Path, required=True)
    parser.add_argument('--local-resources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    restore(args.manifest, args.worker_record, args.local_resources, args.output)
