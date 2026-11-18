#!/usr/bin/env bash
set -euo pipefail
# Run on the local scheduler; runner performs worker/resource checks.
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_dir"
exec uv run python -m egg_harness.runner "$@"
