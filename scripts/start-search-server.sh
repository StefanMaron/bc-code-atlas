#!/usr/bin/env bash
# Search backend: semantic search over the AL + docs corpus (default :8801).
# Not meant to be exposed directly -- point testers at the aggregator instead
# (scripts/start-aggregator.sh), which forwards to this.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# cocoindex-code's shared daemon resolves this project's `chunkers: module:
# al_chunker:al_chunker` setting via a bare `importlib.import_module` -- no
# per-project sys.path insertion exists anywhere in cocoindex-code (it's a
# vendored upstream submodule, not ours to patch -- constitution Principle
# VI), so al_chunker must already be importable from the daemon's own
# sys.path at daemon-start time. Confirmed live: without this, the daemon
# crashes every search request with "ModuleNotFoundError: No module named
# 'al_chunker'" despite the search server process itself starting fine.
# PYTHONPATH is inherited by uv run's subprocess (confirmed: `uv run`
# doesn't strip it) and by the `ccc run-daemon` child it spawns in turn.
export PYTHONPATH="$ROOT/chunker${PYTHONPATH:+:$PYTHONPATH}"

exec uv run --project "$ROOT/tools/cocoindex-code" \
    python "$ROOT/chunker/mcp_http_server.py" \
    "$ROOT/data" \
    --host "${SEARCH_HOST:-127.0.0.1}" \
    --port "${SEARCH_PORT:-8801}"
