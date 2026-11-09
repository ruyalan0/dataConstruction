"""Read actual cgroup v1 limits and GPU inventory, never host free RAM."""
import argparse
import json
import os
from pathlib import Path
import subprocess
parser=argparse.ArgumentParser();parser.add_argument('--cpu-only',action='store_true');args=parser.parse_args()
root=Path('/sys/fs/cgroup')
def read(p):return int((root/p).read_text())
print(json.dumps(dict(cpu=read('cpu/cpu.cfs_quota_us')/read('cpu/cpu.cfs_period_us'),
 memory_limit=read('memory/memory.limit_in_bytes'),memory_used=read('memory/memory.usage_in_bytes'),
 memsw_limit=read('memory/memory.memsw.limit_in_bytes'),swap_devices=Path('/proc/swaps').read_text(),
 worker=os.environ.get('ARNOLD_WORKER_ID'),nas=Path(__file__).resolve().parents[1].is_dir())))
if not args.cpu_only: subprocess.run(['nvidia-smi','--query-gpu=index,name,memory.total,memory.used,utilization.gpu','--format=csv'],check=True)
