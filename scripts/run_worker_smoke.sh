#!/usr/bin/env bash
set -euo pipefail
task_root=/workspace/astra-Robodojo
output_name=${1:?provide a fresh output name}
export LD_LIBRARY_PATH=/usr/local/cuda-12.6/compat:/tmp/astra-driver-535.183.06
unset LD_PRELOAD
export PYTHONPATH=$task_root/src
export OMNI_KIT_ACCEPT_EULA=YES PYTHONNOUSERSITE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export MPLCONFIGDIR=/tmp/astra-mpl
/tmp/robodojo-uv/bin/python "$task_root/scripts/worker_preflight.py"
/tmp/robodojo-uv/bin/python "$task_root/scripts/probe_warp.py" --torch-first --warp-root /tmp/robodojo-uv/lib/python3.11/site-packages/isaacsim/extscache/omni.warp.core-1.8.2+lx64
timeout 300 /tmp/robodojo-uv/bin/python "$task_root/scripts/smoke_sim.py" --robodojo-root /workspace/RoboDojo --assets "$task_root/artifacts/runtime/Assets" --object-assets "$task_root/artifacts/task-assets-v1/Assets/Object/RoboDojo" --output "$task_root/artifacts/$output_name"
/tmp/robodojo-uv/bin/python "$task_root/scripts/validate_smoke.py" "$task_root/artifacts/$output_name"
