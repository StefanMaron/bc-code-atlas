#!/usr/bin/env bash
# Unified MCP endpoint (default :8800) -- point MCP clients and the Cloudflare
# Tunnel at this. Requires the search and graph backends already running
# (start-search-server.sh, start-graph-server.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

args=(
    --host "${AGGREGATOR_HOST:-127.0.0.1}"
    --port "${AGGREGATOR_PORT:-8800}"
    --search-url "http://127.0.0.1:${SEARCH_PORT:-8801}/mcp"
    --graph-url "http://127.0.0.1:${GRAPH_PORT:-8802}/mcp"
)
# Set this to the hostname a Cloudflare Tunnel (or any reverse proxy) fronts
# this server with -- required or every tunneled request gets rejected with
# "Invalid Host header" (see CLOUDFLARE_TUNNEL.md).
if [ -n "${AGGREGATOR_PUBLIC_HOSTNAME:-}" ]; then
    args+=(--public-hostname "$AGGREGATOR_PUBLIC_HOSTNAME")
fi

exec uv run --project "$ROOT/aggregator" \
    python "$ROOT/aggregator/unified_mcp_server.py" \
    "${args[@]}"
