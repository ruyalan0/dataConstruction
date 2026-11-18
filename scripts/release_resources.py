"""Actual cgroup admission for worker-only restoration and simulation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from frozen_runtime import load_runtime


def resources():
    cg = Path('/sys/fs/cgroup')
    if (cg / 'cgroup.controllers').exists():
        quota, period = (cg / 'cpu.max').read_text().split()
        memory = int((cg / 'memory.max').read_text())
        used = int((cg / 'memory.current').read_text())
        swap = (cg / 'memory.swap.max').read_text().strip()
        swap_bytes = None if swap == 'max' else int(swap)
        version = 2
    else:
        quota = (cg / 'cpu/cpu.cfs_quota_us').read_text().strip()
        period = (cg / 'cpu/cpu.cfs_period_us').read_text().strip()
        memory = int((cg / 'memory/memory.limit_in_bytes').read_text())
        used = int((cg / 'memory/memory.usage_in_bytes').read_text())
        swap = (cg / 'memory/memory.memsw.limit_in_bytes').read_text().strip()
        swap_bytes = None if int(swap) > 2**60 else max(0, int(swap) - memory)
        version = 1
    cpu = int(quota) / int(period)
    if cpu <= 0 or memory > 2**60:
        raise ValueError('Finite cgroup CPU and memory quotas are required')
    return {'cpu': cpu, 'memory_limit': memory, 'memory_used': used, 'cgroup_version': version,
            'swap_limit_bytes': swap_bytes, 'swap_quota_raw': swap,
            'gpu_device_nodes': sorted(p.name for p in Path('/dev').glob('nvidia[0-9]*') if p.is_char_device()),
            'swap_devices': Path('/proc/swaps').read_text()}


def driver_environment():
    release = Path(__file__).resolve().parents[1] / 'release'
    hashes = json.loads((release / 'manifest.json').read_text())['files']
    expected = {name: hashes['release/worker/' + name] for name in ('worker_driver.py', 'restore_worker_runtime.py')}
    prepare = load_runtime(release / 'worker', expected).prepare
    parent = Path(tempfile.mkdtemp(prefix='physevo-admission-driver-'))
    driver = prepare(Path('/proc/driver/nvidia/version').read_text(),
                     Path('/usr/lib/x86_64-linux-gnu'), parent / 'lib')
    env = dict(os.environ, LD_LIBRARY_PATH='/usr/local/cuda-12.6/compat:' + driver['directory'])
    env.pop('LD_PRELOAD', None)
    return env


def admit(local_path, scratch):
    worker_id = os.environ.get('ARNOLD_WORKER_ID')
    if not worker_id:
        raise RuntimeError('Run this command on a running GPU worker, not the controller Pod')
    local = json.loads(Path(local_path).read_text())
    worker = resources()
    if worker['cpu'] <= local['cpu'] or worker['memory_limit'] <= local['memory_limit']:
        raise ValueError('Worker cgroup quotas must exceed controller quotas')
    if local['gpu_device_nodes']:
        raise ValueError('This placement plan expects a controller without GPUs')
    comparison = {name: {'controller': local[name], 'worker': worker[name]}
                  for name in ('cpu', 'memory_limit', 'swap_limit_bytes', 'gpu_device_nodes')}
    # Swap is compared and recorded, but never counted as additional RAM.
    # Unlimited memsw is normal on the known worker even with no swap devices.
    if worker['memory_limit'] - worker['memory_used'] < 32 * 1024**3:
        raise ValueError('Worker needs 32 GiB available within its cgroup quota')
    if not Path(scratch).is_dir() or shutil.disk_usage(scratch).free < 100 * 1024**3:
        raise ValueError('Worker scratch must exist and have 100 GiB free')
    text = subprocess.check_output(['nvidia-smi', '--query-gpu=index,name,memory.total,memory.used',
                                    '--format=csv,noheader,nounits'], env=driver_environment(), text=True)
    gpus = [dict(zip(('index', 'name', 'total_mib', 'used_mib'), map(str.strip, row.split(','))))
            for row in text.splitlines()]
    if not gpus or any('L20' not in gpu['name'] for gpu in gpus):
        raise ValueError('This release is qualified for NVIDIA L20 workers')
    return {'worker_id': worker_id, 'local': local, 'worker': worker, 'gpus': gpus,
            'comparison': comparison, 'scratch_free_bytes': shutil.disk_usage(scratch).free}
