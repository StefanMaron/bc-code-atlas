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
# VI), so al_chunker (and its own real deps, tree-sitter/tree-sitter-al)
# must already be importable from the daemon's own environment at
# daemon-start time. Confirmed live: without this, the daemon crashes every
# search request with "ModuleNotFoundError" (first for al_chunker itself,
# then -- after a PYTHONPATH-only fix that solved that half but not the
# other -- for tree_sitter) despite the search server process itself
# starting fine. `--with-editable` builds an ephemeral overlay venv merging
# cocoindex-code's own locked deps with chunker/'s (bc-al-chunker) real
# ones and runs this command through it; the `ccc run-daemon` child this
# process spawns in turn locates its own `ccc` executable via
# `Path(sys.executable).parent` (cocoindex_code/client.py), which resolves
# to that same overlay venv, so the daemon inherits it too -- confirmed
# live. Deliberately not a plain `uv pip install` into the venv: that would
# get silently pruned back out by the next `uv sync --project
# tools/cocoindex-code` (every deploy runs one) since it's not a locked
# dependency of the vendored project.
exec uv run --project "$ROOT/tools/cocoindex-code" --with-editable "$ROOT/chunker" \
    python "$ROOT/chunker/mcp_http_server.py" \
    "$ROOT/data" \
    --host "${SEARCH_HOST:-127.0.0.1}" \
    --port "${SEARCH_PORT:-8801}"
