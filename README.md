# bc-code-atlas

A queryable window into Microsoft Dynamics 365 Business Central's AL source
code and official documentation — for dependency and implementation
investigation before writing or reviewing AL customizations. Point any
MCP-capable coding agent at it and ask things like *"where does BC validate a
sales order before posting?"* or *"what subscribes to `OnBeforePostSalesDoc`?"*
and get real answers grounded in Microsoft's own base-application source, not
guesses from training data.

This started as a local, single-version proof of concept — see
[REPORT.md](REPORT.md) for that phase's findings, benchmarks, and go
recommendation. The project has since graduated into building the real
multi-country, multi-version public service; see
[.specify/memory/constitution.md](.specify/memory/constitution.md) for the
governing architecture principles and [CLAUDE.md](CLAUDE.md) for current
status and history. What's below (Quick Start, architecture diagram) still
describes today's actual runnable setup — single version, local only —
since the multi-tenant serving layer is being designed, not built yet.

## Architecture

Two independent layers, plus a thin aggregator so testers only need one URL:

```
                      ┌─────────────────────────┐
MCP client ────────▶  │  aggregator (:8800)      │
                      │  one /mcp endpoint,       │
                      │  forwards to both below   │
                      └─────────┬─────────┬───────┘
                                │         │
                    ┌───────────▼──┐  ┌───▼────────────┐
                    │ search (:8801)│  │ graph (:8802)  │
                    │ cocoindex-code│  │ graphify-al    │
                    │ + AL chunker  │  │                │
                    │ semantic layer│  │ structural layer│
                    └───────────────┘  └────────────────┘
```

- **Search** (`chunker/`, backed by the `tools/cocoindex-code` submodule) —
  semantic search over the w1-28 base-application AL source plus two docs
  sources: `dynamics365smb-docs` (`business-central/`, functional/admin
  docs) and `dynamics365smb-devitpro-pb` (`dev-itpro/developer/`, the AL
  language/compiler reference -- diagnostics, properties, methods). The
  original brief named this second repo `dynamics365smb-devitpro`; that
  repo no longer exists under that name, it's now the `-pb` (public) repo
  above. Uses a custom AL-aware chunker (`chunker/al_chunker.py`, built on
  `tree-sitter-al`) so AL chunks align with real objects/procedures instead
  of naive line splitting.
- **Graph** (`tools/graphify-al` submodule) — the exact structural
  relationship graph: objects, procedures, event subscriptions, extension
  targets, with real (not inferred) call/subscribe/extend edges extracted
  from source. Also serves exact source text on demand
  (`bcatlas_get_signature`, `bcatlas_get_procedure_body`,
  `bcatlas_get_object_source`), re-read from the real w1-28 files rather
  than the index, for verifying a candidate before trusting it.
- **Aggregator** (`aggregator/`) — a thin proxy presenting one `/mcp`
  endpoint. No business logic lives here; it forwards each tool call to
  whichever backend implements it. The two backends stay independent and
  swappable — this is deliberate, see CLAUDE.md's rationale for why one tool
  doesn't do both jobs well.

All tool names are prefixed with `bcatlas_` (e.g. `bcatlas_search`,
`bcatlas_get_neighbors`) — plain names like `search` collide with
IDE-builtin tools (VS Code's own search, in particular) in some MCP
clients.

Everything runs locally. No cloud APIs are called at query time (the
embedding model does a one-time HuggingFace metadata check on first load,
even though it's already cached locally).

## Quick start

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone --recurse-submodules <this-repo-url>
cd bc-code-atlas

# 0. The two docs submodules point at full Microsoft docs repos (functional
#    docs + the separate AL developer/compiler reference); only a subtree of
#    each is actually relevant here, so restrict each to a sparse checkout
#    before indexing -- this keeps clone/index size and time reasonable.
git -C data/docs sparse-checkout init --cone && git -C data/docs sparse-checkout set business-central
git -C data/docs-devitpro sparse-checkout init --cone && git -C data/docs-devitpro sparse-checkout set dev-itpro/developer

# 1. Build each subproject's venv
uv sync --project tools/cocoindex-code
uv sync --project tools/graphify-al
uv sync --project chunker
uv sync --project aggregator

# 2. Configure the embedding model (local, free, offline after first download)
#    ~/.cocoindex_code/global_settings.yml:
#      provider: sentence-transformers
#      model: ibm-granite/granite-embedding-97m-multilingual-r2
#      device: cuda   # or cpu -- see "GPU vs CPU" below

# 3. Index the corpus (see "GPU vs CPU" -- this step wants a GPU)
cd data && uv run --project ../tools/cocoindex-code ccc index && cd ..

# 4. Extract the structural graph
cd tools/graphify-al && uv run python -m graphify update ../../data/w1-28-src && cd ../..

# 5. Start all three servers (each blocks -- run in separate terminals,
#    or background them, e.g. with nohup/systemd/tmux)
./scripts/start-search-server.sh    # :8801
./scripts/start-graph-server.sh     # :8802
./scripts/start-aggregator.sh       # :8800 -- point clients here

# 6. Point any MCP client at the aggregator
```

```json
{
  "mcpServers": {
    "bc-code-atlas": { "type": "http", "url": "http://localhost:8800/mcp" }
  }
}
```

`client-session/` has a worked example (`.mcp.json` plus two full
transcripts of real queries run against a separate Claude Code session,
matching CLAUDE.md's two validation scenarios).

## GPU vs CPU

Benchmarked directly (see REPORT.md for the raw numbers):

| Phase | GPU | CPU | Verdict |
|---|---|---|---|
| Query-time (one search) | ~8ms | ~10ms | No real difference — serving doesn't need a GPU |
| Full corpus reindex (250K chunks) | ~3 min | ~20 hours | GPU wins by ~400x — indexing wants one |

The graph server (`graphify-al`) has zero ML dependencies at all — it's
CPU-portable with no changes, always. Practical setup for a CPU-only
always-on host: build the index once on a GPU machine, copy the resulting
`data/.cocoindex_code/*.db` files over, then serve from the CPU box.
Day-to-day incremental reindexing (small deltas as upstream changes) is
fast enough to run directly on CPU.

## What's excluded from this repo, and why

- **Third-party source is never vendored** — `data/w1-28-src`, `data/docs`,
  `data/docs-devitpro`, `tools/cocoindex-code`, `tools/tree-sitter-al`,
  `tools/graphify-al` are git
  submodules pointing at their real upstreams/forks. `tools/graphify-al`
  points at a fork branch (`StefanMaron/graphify-al@bc-code-atlas-fixes`)
  with real bug fixes found while building this PoC (directed-graph
  traversal, ranking) — see REPORT.md finding #7.
- **Generated index data is gitignored** — the SQLite DBs under
  `data/.cocoindex_code/` and every `graphify-out/graph.json` are rebuilt
  locally per the Quick Start above, not committed.
- Two structural-layer alternatives were evaluated and set aside (see
  CLAUDE.md's "Open decision" and REPORT.md) rather than vendored here:
  [`StefanMaron/AL-Dependency-MCP-Server`](https://github.com/StefanMaron/AL-Dependency-MCP-Server)
  and [`SShadowS/graphify`](https://github.com/SShadowS/graphify) (branch
  `al-language-support`).

## Exposing this publicly

See [CLOUDFLARE_TUNNEL.md](CLOUDFLARE_TUNNEL.md) for a guided walkthrough —
one Cloudflare Tunnel hostname routed at the aggregator's `:8800`, with a
Cloudflare Access gate in front so only invited testers can reach it. The
MCP servers themselves have no built-in auth beyond graphify-al's optional
`--api-key`/`GRAPHIFY_API_KEY` flag (unset by default) — treat the tunnel's
Access gate as the real access control.

## License

MIT — see [LICENSE](LICENSE).
