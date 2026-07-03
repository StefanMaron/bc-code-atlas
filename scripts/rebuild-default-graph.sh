#!/usr/bin/env bash
# Rebuilds the default corpus's structural graph (data/w1-28-src/graphify-out).
# Not incremental -- a full re-extraction, same as the README's manual steps
# this wraps. Run this, then restart start-graph-server.sh to serve the result.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEARCH_DIR="$ROOT/data/w1-28-src"

# Keeps document/media files (READMEs, PDFs, images, ...) out of the
# structural AL code graph -- graphify-al is a general-purpose, multi-modal
# tool that otherwise extracts "document" nodes from them right alongside
# real AL code nodes (see build/build/graphify.ignore.template for the full
# rationale and the live incident that found this). $SEARCH_DIR is a git
# submodule checkout, not a place to hand-maintain this file -- write it
# fresh every rebuild instead of relying on it surviving a reset/re-clone.
cp "$ROOT/build/build/graphify.ignore.template" "$SEARCH_DIR/.graphifyignore"

cd "$ROOT/tools/graphify-al"
exec uv run python -m graphify update "$SEARCH_DIR"
