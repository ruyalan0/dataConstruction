"""Read actual Pod quotas, GPU inventory and shared source visibility."""

import json
import subprocess
from pathlib import Path

root = Path("/sys/fs/cgroup")
paths = [
    "cpu/cpu.cfs_quota_us",
    "cpu/cpu.cfs_period_us",
    "memory/memory.limit_in_bytes",
    "memory/memory.usage_in_bytes",
    "memory/memory.memsw.limit_in_bytes",
]
data = {p: (root / p).read_text().strip() for p in paths}
data["swap_devices"] = Path("/proc/swaps").read_text()
data["nas_visible"] = (Path(__file__).resolve().parents[1] / "docs/my-plan.md").is_file()
gpu = subprocess.run(
    ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu", "--format=csv"],
    capture_output=True,
    text=True,
    check=True,
)
data["gpu"] = gpu.stdout
print(json.dumps(data, indent=2))
