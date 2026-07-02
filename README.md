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
multi-country, multi-version public service (see
[.specify/memory/constitution.md](.specify/memory/constitution.md) for the
governing architecture principles and [CLAUDE.md](CLAUDE.md) for current
status and history) — **and that build has landed**: any (country, version)
pair in the real upstream source-history repository can be discovered,
resolved, diffed, and built/served on demand, not just the one `w1-28`
corpus indexed at setup time. `w1-28` remains the always-warm default
corpus every search/graph tool falls back to when no `country`/`version` is
given, so nothing about the original single-version workflow changed for a
caller that doesn't opt in to the rest.

## Architecture

Four independent layers, plus a thin aggregator so clients only need one URL:

```
                      ┌───────────────────────────┐
MCP client ────────▶  │  aggregator (:8800)        │
                      │  one /mcp endpoint,         │
                      │  forwards to all four below │
                      └───┬───────┬───────┬─────┬───┘
                          │       │       │     │
              ┌───────────▼─┐ ┌───▼──────┐ ┌────▼─────┐ ┌─▼──────────┐
              │ search(:8801)│ │graph(:8802)│ │registry  │ │ build      │
              │ cocoindex-code│ │graphify-al │ │(:8803)   │ │ (:8804)    │
              │ + AL chunker  │ │            │ │discover/ │ │ on-demand  │
              │ multi-tenant  │ │multi-tenant│ │resolve/  │ │ build+serve│
              │ semantic layer│ │struct. layer│ │diff/hist.│ │ split      │
              └───────────────┘ └────────────┘ └──────────┘ └────────────┘
```

- **Search** (`chunker/`, backed by the `tools/cocoindex-code` submodule) —
  semantic search over AL source plus two docs sources:
  `dynamics365smb-docs` (`business-central/`, functional/admin docs) and
  `dynamics365smb-devitpro-pb` (`dev-itpro/developer/`, the AL
  language/compiler reference -- diagnostics, properties, methods). The
  original brief named this second repo `dynamics365smb-devitpro`; that
  repo no longer exists under that name, it's now the `-pb` (public) repo
  above. Uses a custom AL-aware chunker (`chunker/al_chunker.py`, built on
  `tree-sitter-al`) so AL chunks align with real objects/procedures instead
  of naive line splitting. Multi-tenant: an optional `country`/`version`
  pair on `bcatlas_search` routes to a specific built (country, version)
  instead of the default `w1-28` corpus.
- **Graph** (`tools/graphify-al` submodule) — the exact structural
  relationship graph: objects, procedures, event subscriptions, extension
  targets, with real (not inferred) call/subscribe/extend edges extracted
  from source. Also serves exact source text on demand
  (`bcatlas_get_signature`, `bcatlas_get_procedure_body`,
  `bcatlas_get_object_source`), re-read from the real source files rather
  than the index, for verifying a candidate before trusting it. Multi-tenant
  the same way as search.
- **Registry** (`registry/`) — country/version discovery and resolution
  (`bcatlas_list_countries`, `bcatlas_list_versions`, `bcatlas_resolve_version`)
  against the real upstream source-history repository, plus version-aware
  diffing (`bcatlas_diff`, scoped to a file or a resolved symbol -- never
  unscoped) and multi-step symbol change-history (`bcatlas_symbol_history`,
  only real change points, never every commit that merely touched a shared
  file). No database — git itself is the source of truth.
- **Build** (`build/`) — on-demand build/serve of a (country, version) pair
  not yet warm (`bcatlas_request_version`, `bcatlas_version_status`).
  Staging + atomic promote (a build-writer and a serve-reader never touch
  the same on-disk artifact concurrently -- see constitution Principle II),
  a bounded GPU-aware build queue with request coalescing, clone-and-patch
  incremental builds reusing cocoindex-code's own stock incremental
  indexing against the nearest already-warm sibling, and LRU/TTL eviction
  of idle warm data (always safe to reclaim -- historical versions are
  immutable and re-buildable, constitution Principle III).
- **Aggregator** (`aggregator/`) — a thin proxy presenting one `/mcp`
  endpoint. No business logic lives here; it forwards each tool call to
  whichever backend implements it, passing `country`/`version` through
  unchanged when supplied. The four backends stay independent and
  swappable — this is deliberate, see CLAUDE.md's rationale for why one tool
  doesn't do both jobs well.

**Using a specific (country, version) instead of the default corpus:**
resolve it first (`bcatlas_resolve_version`), request it if not already
warm (`bcatlas_request_version`, poll `bcatlas_version_status` until
`ready`), then pass the returned **`commit_sha`** — not `version_string` —
as `version` (together with `country`) to any search/graph tool. See
`specs/001-multi-version-serving/quickstart.md` for a full worked walkthrough.

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
uv sync --project registry
uv sync --project build

# 2. Configure the embedding model (local, free, offline after first download)
#    ~/.cocoindex_code/global_settings.yml:
#      provider: sentence-transformers
#      model: ibm-granite/granite-embedding-97m-multilingual-r2
#      device: cuda   # or cpu -- see "GPU vs CPU" below

# 3. Index the corpus (see "GPU vs CPU" -- this step wants a GPU)
cd data && uv run --project ../tools/cocoindex-code ccc index && cd ..

# 4. Extract the structural graph
cd tools/graphify-al && uv run python -m graphify update ../../data/w1-28-src && cd ../..

# 5. Start all five servers (each blocks -- run in separate terminals,
#    or background them, e.g. with nohup/systemd/tmux)
./scripts/start-search-server.sh    # :8801 -- default w1-28 corpus
./scripts/start-graph-server.sh     # :8802 -- default w1-28 graph
./scripts/start-registry-server.sh  # :8803 -- discover/resolve/diff/history
./scripts/start-build-server.sh     # :8804 -- on-demand build/serve
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

## Usage

Everything below is a single MCP tool call through the aggregator
(`http://localhost:8800/mcp`, or wherever it's exposed) — no direct
filesystem or backend access, ever, even for a local deployment (constitution
Principle I). All tool names are prefixed `bcatlas_`.

### Capabilities

| Tool | What it does |
|---|---|
| `bcatlas_search` | Semantic search over AL source + docs by meaning, not text matching |
| `bcatlas_query_graph` | Broad BFS/DFS traversal of the structural graph from a natural-language question |
| `bcatlas_get_node` | Full details for one object/procedure node |
| `bcatlas_get_neighbors` | Everything that calls/subscribes to/references a node |
| `bcatlas_get_signature` | Exact declaration header only — cheap check before pulling a full body |
| `bcatlas_get_procedure_body` | Exact, full source of one procedure/trigger, re-read from real source |
| `bcatlas_get_object_source` | Exact, full source of an entire object |
| `bcatlas_get_community` / `bcatlas_god_nodes` / `bcatlas_graph_stats` | Graph-wide exploration and stats |
| `bcatlas_shortest_path` | Shortest structural path between two BC concepts |
| `bcatlas_list_countries` | List available country localizations |
| `bcatlas_list_versions` | List a country's available major versions (summarized, not raw builds) |
| `bcatlas_resolve_version` | Resolve an exact or loose version spec to one unambiguous build |
| `bcatlas_diff` | File- or symbol-scoped diff between two versions of the same country |
| `bcatlas_symbol_history` | Every real point a specific symbol's own text changed across a version range |
| `bcatlas_request_version` | Build/warm a (country, version) pair not yet available |
| `bcatlas_version_status` | Poll a requested build's state |

### Querying the default corpus (`w1-28`, always warm)

No setup needed — just call `bcatlas_search`/`bcatlas_query_graph`/etc.
directly, exactly as in `client-session/`'s worked transcripts:

```
bcatlas_search(query="sales order posting validation", limit=5)
bcatlas_query_graph(question="what subscribes to OnBeforePostSalesDoc")
bcatlas_get_procedure_body(label="Codeunit 80 PostSalesDoc")
```

### Querying a different country or version

1. **Discover**, if you don't already know what's available:
   `bcatlas_list_countries()` → `bcatlas_list_versions(country="us")`.
2. **Resolve** a spec (exact build, exact commit, or loose `"major.minor"`
   like `"28.1"`) to one unambiguous build:
   `bcatlas_resolve_version(country="us", spec="28.1")` →
   `{resolved: true, commit_sha: "...", version_string: "us-28.1...."}`.
3. **Request** it if not already warm, then **poll** until ready:
   `bcatlas_request_version(country="us", spec="28.1")` → `{status: "queued"|"in_progress"|"ready", commit_sha: "..."}`,
   then `bcatlas_version_status(country="us", commit_sha="...")` until `{state: "ready"}`.
   A brand-new (country, version) pair can take real time to build (GPU-bound,
   queued); a version close to something already warm is typically much
   faster thanks to incremental reuse.
4. **Query** it — pass `country` and the resolved **`commit_sha`** (not
   `version_string`) as `version` to any search/graph tool:
   `bcatlas_search(query="...", country="us", version="<commit_sha>")`.

### Comparing versions

```
bcatlas_diff(country="w1", from_spec="28.1", to_spec="28.2",
             object_type="codeunit", object_name="Sales-Post", procedure_name="PostSalesDoc")

bcatlas_symbol_history(country="w1", from_spec="28.1", to_spec="28.2",
                        object_type="codeunit", object_name="Sales-Post",
                        procedure_name="PostSalesDoc", granularity="full")
```

`bcatlas_diff` always requires a scope (`path`, or `object_type`+
`object_name`) — an unscoped, whole-repository diff is refused outright, it
was measured to be far too large to be useful (hundreds of files even
across a single minor-version span). See
`specs/001-multi-version-serving/quickstart.md` for a complete worked
walkthrough of all three flows above.

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
  locally per the Quick Start above, not committed. The multi-version
  runtime state under `data/.upstream-mirror/`, `data/warm/`, and
  `data/staging/` is likewise gitignored and fully rebuildable — every
  historical (country, version) pair is immutable and re-fetchable from the
  real upstream repository at any time (constitution Principle III).
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
