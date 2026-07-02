#!/usr/bin/env bash
# Build/serve control plane: on-demand (country, version) build queue and
# status polling (default :8804). Not meant to be exposed directly -- point
# testers at the aggregator instead (scripts/start-aggregator.sh), which
# forwards to this once wired (see specs/001-multi-version-serving/tasks.md
# T031 -- not done by this script's own task).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec uv run --project "$ROOT/build" \
    python -m build.mcp_server \
    --host "${BUILD_HOST:-127.0.0.1}" \
    --port "${BUILD_PORT:-8804}" \
    --data-dir "${BCATLAS_DATA_DIR:-$ROOT/data}"
