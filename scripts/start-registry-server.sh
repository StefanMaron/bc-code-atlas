#!/usr/bin/env bash
# Registry backend: version discovery/resolution over every country/version
# in the real upstream source-history repository (default :8803).
# Not meant to be exposed directly -- point testers at the aggregator instead
# (scripts/start-aggregator.sh), which forwards to this.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec uv run --project "$ROOT/registry" \
    python -m registry.mcp_server \
    --host "${REGISTRY_HOST:-127.0.0.1}" \
    --port "${REGISTRY_PORT:-8803}"
