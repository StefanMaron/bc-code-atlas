#!/usr/bin/env bash
# Deploy bc-code-atlas's serving side to this VM: pull master, resync every
# subproject's venv, restart the systemd services. Invoked by
# .github/workflows/deploy.yml over SSH on every push to master (gated by
# ci.yml passing first via branch protection), or manually for a one-off
# redeploy. Idempotent -- safe to re-run.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> git pull"
git fetch --quiet origin master
git reset --hard origin/master
git submodule update --init --recursive

echo "==> uv sync (all subprojects)"
for p in tools/cocoindex-code chunker aggregator registry build; do
  uv sync --project "$p"
done
# tools/graphify-al's AL support (tree-sitter-al) is an optional extra
# there (graphify is multi-language, AL is opt-in) but not optional for
# this project -- always sync it in. chunker's own tree-sitter-al pin is
# already a hard dependency in chunker/pyproject.toml, no extra needed.
uv sync --project tools/graphify-al --extra al

echo "==> restart services"
sudo systemctl restart bcatlas.target

echo "==> deploy complete: $(git rev-parse --short HEAD)"
