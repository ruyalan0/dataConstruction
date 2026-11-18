#!/usr/bin/env bash
# Local setup only; the frozen release/harness files remain unchanged.
set -euo pipefail
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
COPY_ROOT=$(dirname -- "$PROJECT_ROOT")
export TMPDIR="$COPY_ROOT/.runtime/tmp"
export ISAACSIM_PORTABLE_ROOT="$COPY_ROOT/.runtime/isaacsim"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$TMPDIR" "$ISAACSIM_PORTABLE_ROOT"

# These checkouts were created by zhangzeyu for lanruya. Trust only these
# three exact paths for child git commands, without editing ~/.gitconfig.
config_count=${GIT_CONFIG_COUNT:-0}
for checkout in "$PROJECT_ROOT" "$PROJECT_ROOT/dependencies/RoboDojo" \
                "$PROJECT_ROOT/dependencies/RoboDojo/XPolicyLab"; do
  export "GIT_CONFIG_KEY_${config_count}=safe.directory"
  export "GIT_CONFIG_VALUE_${config_count}=$checkout"
  config_count=$((config_count + 1))
done
export GIT_CONFIG_COUNT=$config_count

cd -- "$PROJECT_ROOT"
exec "$PROJECT_ROOT/.venv-policy/bin/python" -u -B \
  "$PROJECT_ROOT/scripts/run_release_pickup_local.py" \
  --api-config "${ROBO_API_CONFIG:-/home/lanruya/.codex/config.toml}" \
  --api-auth "${ROBO_API_AUTH:-/home/lanruya/.codex/auth.json}" \
  "$@"
