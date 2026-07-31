#!/usr/bin/env bash
# Deploy bc-code-atlas's serving side to this VM: pull master, resync every
# subproject's venv, restart the systemd services. Invoked by
# .github/workflows/deploy.yml over SSH on every push to master (gated by
# ci.yml passing first via branch protection), or manually for a one-off
# redeploy. Idempotent -- safe to re-run.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# The forced-command SSH session this runs under (see the CD deploy key's
# authorized_keys entry) isn't a login shell, so ~/.local/bin (where uv
# installs) isn't on PATH by default -- confirmed live, the first real CD
# run failed with "uv: command not found". uv's own installer-generated env
# script adds it idempotently.
[ -f "$HOME/.local/bin/env" ] && . "$HOME/.local/bin/env"

echo "==> git pull"
git fetch --quiet origin master
git reset --hard origin/master
git submodule update --init --recursive

echo "==> uv sync (all subprojects)"
for p in tools/cocoindex-code chunker aggregator registry build; do
  uv sync --project "$p"
done
# tools/graphify-al's AL support (tree-sitter-al) and MCP HTTP transport
# (uvicorn/starlette) are both optional extras there (graphify is
# multi-language and multi-transport by default) but neither is optional
# for this project -- always sync both in. chunker's own tree-sitter-al
# pin is already a hard dependency in chunker/pyproject.toml, no extra
# needed there.
uv sync --project tools/graphify-al --extra al --extra mcp

echo "==> restart services"
sudo systemctl restart bcatlas.target

echo "==> deploy complete: $(git rev-parse --short HEAD)"
