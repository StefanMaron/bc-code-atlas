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

# Everything that follows is wrapped in a function and invoked at the very
# end (`main`), not run inline. `git reset --hard` below rewrites this very
# file on disk while bash is still reading it -- run inline, that corrupts
# bash's read offset mid-script (confirmed live: a real CD run partway
# through printed a garbled "line 18: uv: command not found" despite uv
# being sourced onto PATH just above). Bash parses a function body as one
# block up front, before executing any of it, so it's immune to the
# underlying file changing after parsing.
main() {
  # Captured before `git reset --hard` moves HEAD, so we can tell below
  # whether this deploy actually touched the search daemon's own code.
  local old_head
  old_head="$(git rev-parse HEAD)"

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

  # Idempotent: keeps /etc/systemd/system/bcatlas-search.service.d/'s
  # KillMode=process override in sync with the tracked copy (see that
  # file's own comment for the full why). daemon-reload is a no-op if
  # nothing changed.
  echo "==> sync systemd overrides"
  sudo mkdir -p /etc/systemd/system/bcatlas-search.service.d
  sudo cp "$ROOT/scripts/systemd/bcatlas-search.service.d/override.conf" \
    /etc/systemd/system/bcatlas-search.service.d/override.conf
  sudo systemctl daemon-reload

  # With KillMode=process in effect, `systemctl restart bcatlas.target`
  # below no longer kills the search daemon (`ccc run-daemon`) -- it
  # survives and keeps serving from its already-warm state, which is the
  # whole point (see the override's own comment). But that also means a
  # code change to al_chunker or cocoindex-code itself wouldn't take
  # effect until the daemon actually restarts -- so when this deploy
  # touched either, explicitly kill the daemon's whole cgroup first
  # (bypassing the override for this one intentional case via
  # `--kill-whom=all`) so the restart below picks up the new code instead
  # of silently continuing to serve with the old daemon.
  if ! git diff --quiet "$old_head" HEAD -- chunker/ tools/cocoindex-code; then
    echo "==> chunker/cocoindex-code changed -- killing the search daemon so it picks up the new code"
    sudo systemctl kill --kill-whom=all --signal=SIGTERM bcatlas-search.service || true
    sleep 2
    sudo systemctl kill --kill-whom=all --signal=SIGKILL bcatlas-search.service || true
  fi

  echo "==> restart services"
  sudo systemctl restart bcatlas.target

  # Usually near-instant now (the daemon survived the restart above and is
  # already warm). Only pays a real cost on a genuine cold start: first
  # deploy after this change, a VM reboot, a crash, or the explicit kill
  # just above -- a fresh daemon's first search pays a genuine, unavoidable
  # full corpus reprocess (see chunker/chunking.py's CHUNKER_REGISTRY
  # comment) that ran well past 2 hours in a clean live measurement this
  # session on this VM's CPU-only hardware, not the ~30 minutes originally
  # estimated. Waiting for it here either way (instead of letting the
  # first live user pay it, and letting "deploy complete" below lie about
  # actual readiness) is the whole point of this step.
  echo "==> waiting for search index to warm up"
  python3 "$ROOT/scripts/wait-for-search-ready.py"

  echo "==> deploy complete: $(git rev-parse --short HEAD)"
}

main "$@"
