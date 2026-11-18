#!/usr/bin/env bash
set -euo pipefail
task_root=${ASTRA_SOURCE_ROOT:-/workspace/astra-Robodojo}
artifact_root=${ASTRA_ARTIFACT_ROOT:-$task_root/artifacts}
output_name=${1:?new output name required}
steps=${2:-0}
calls=${3:-0}
task_name=${4:-align_blocks}
resume_args=()
if [[ "${ASTRA_RTX_VERSION_WORKAROUND:-0}" == "1" ]]; then
  resume_args+=(--rtx-driver-version-workaround)
fi
if [[ -n "${5:-}" ]]; then resume_args+=(--resume "$5"); fi
export LD_LIBRARY_PATH=${ASTRA_DRIVER_LIBRARY_PATH:-/usr/local/cuda-12.6/compat:/tmp/astra-driver-535.183.06}
unset LD_PRELOAD
export PYTHONPATH=$task_root/src
export OMNI_KIT_ACCEPT_EULA=YES PYTHONNOUSERSITE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MPLCONFIGDIR=/tmp/astra-mpl
/tmp/robodojo-uv/bin/python "$task_root/scripts/worker_preflight.py"
/tmp/robodojo-uv/bin/python "$task_root/scripts/smoke_sim.py" --robodojo-root /workspace/RoboDojo --assets "$artifact_root/runtime/Assets" --object-assets "$artifact_root/task-assets-v1/Assets/Object/RoboDojo" --output "$artifact_root/$output_name" --astra-python "$task_root/.venv/bin/python" --mode episode --max-steps "$steps" --max-calls "$calls" --protocol json-high --image-budget 6 --harness-profile "${HARNESS_PROFILE:-evidence}" --task "$task_name" "${resume_args[@]}"
"$task_root/.venv/bin/python" "$task_root/scripts/validate_episode.py" "$artifact_root/$output_name"
