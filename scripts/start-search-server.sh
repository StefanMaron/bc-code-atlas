#!/usr/bin/env bash
# Search backend: semantic search over the AL + docs corpus (default :8801).
# Not meant to be exposed directly -- point testers at the aggregator instead
# (scripts/start-aggregator.sh), which forwards to this.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec uv run --project "$ROOT/tools/cocoindex-code" \
    python "$ROOT/chunker/mcp_http_server.py" \
    "$ROOT/data" \
    --host "${SEARCH_HOST:-127.0.0.1}" \
    --port "${SEARCH_PORT:-8801}"
