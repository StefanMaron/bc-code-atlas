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
for p in tools/cocoindex-code tools/graphify-al chunker aggregator registry build; do
  uv sync --project "$p"
done

# tree-sitter-al isn't a hard pyproject dependency of graphify-al (AL support
# is optional there) and chunker's own tree-sitter-al pin needs this
# explicit install step to land in its own venv -- see chunker/pyproject.toml.
echo "==> tree-sitter-al (chunker, graphify-al)"
uv pip install --directory chunker "tree-sitter-al>=3.0.1"
uv pip install --directory tools/graphify-al "tree-sitter-al>=3.0.1"

echo "==> restart services"
sudo systemctl restart bcatlas.target

echo "==> deploy complete: $(git rev-parse --short HEAD)"
