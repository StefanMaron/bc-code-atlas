# CLAUDE.md — bc-code-atlas

## Mission

Build and operate a public, MCP-queryable code+docs index of Microsoft
Dynamics 365 Business Central (AL language) — semantic search over source
and docs, plus an exact structural relationship graph (`calls`,
`subscribes`, `extends`) — reachable by any BC/AL developer's coding agent,
across every country localization and every shipped version, not just one.

This project has graduated past its original single-version local
proof-of-concept (see `REPORT.md` for that phase's findings — setup
friction, indexing benchmarks, real test-scenario transcripts, and a go
recommendation) into building the real thing. **The project constitution at
`.specify/memory/constitution.md` is now the authoritative source for
architectural principles** — read it first. This file is operational
context and history: what's been learned, what's already built, and
pointers to where the durable rules live. Where anything below conflicts
with the constitution, the constitution wins.

Work on new features goes through spec-kit
(`/speckit-specify` → `/speckit-plan` → `/speckit-tasks` →
`/speckit-implement`), not by appending ad hoc instructions to this file.

You are a fresh agent with no memory of the conversations that produced
this brief. Ask the user (via chat, not by guessing) if something here is
ambiguous enough to block a real decision — otherwise use judgment and
proceed.

## Where things stand today

**Built and running** — the multi-country/multi-version serving layer
(spec `specs/001-multi-version-serving/`) is implemented and verified live,
end to end, through the aggregator:
- `w1-28` AL source + two docs corpora (`dynamics365smb-docs`
  business-central docs, `dynamics365smb-devitpro-pb` AL developer/compiler
  reference) indexed via a custom `tree-sitter-al` chunker into
  `cocoindex-code`, served as MCP over HTTP (`chunker/`) — still the
  always-warm default corpus every search/graph tool falls back to.
- The structural graph extracted via the `graphify-al` fork, served as MCP
  over HTTP (`tools/graphify-al`), including on-demand exact-source lookup
  tools (`bcatlas_get_signature`, `bcatlas_get_procedure_body`,
  `bcatlas_get_object_source`) that re-parse real source on request rather
  than caching text in the graph.
- **Registry** (`registry/`, `:8803`) — `bcatlas_list_countries`,
  `bcatlas_list_versions`, `bcatlas_resolve_version` (real git ls-remote/log
  against the upstream repo, no new database); `bcatlas_diff` (file- or
  symbol-scoped, rejects unscoped requests) and `bcatlas_symbol_history`
  (multi-step change chain, filters out commits that touched a symbol's
  file without changing the symbol's own text).
- **Build** (`build/`, `:8804`) — `bcatlas_request_version` /
  `bcatlas_version_status`: staging + atomic promote, bounded GPU-aware
  build queue with request coalescing, clone-and-patch incremental builds
  against the nearest already-warm sibling (reuses cocoindex-code's stock
  incremental `ccc index`, no fork), LRU/TTL eviction of idle warm data.
- `chunker/mcp_http_server.py` and `tools/graphify-al/graphify/serve.py`
  are now multi-tenant: every search/graph tool accepts optional
  `country`/`version` (the exact `commit_sha`, not `version_string`) to
  route to a specific built pair instead of the default corpus.
- A thin aggregator (`aggregator/`) presenting one `/mcp` endpoint to
  clients, forwarding to all four backends and passing `country`/`version`
  through unchanged.
- Real tester validation against both original test scenarios plus organic
  use; see `REPORT.md` for the full account and `README.md` for the
  current architecture diagram and local Quick Start.

**Verified live this build** (not simulated — a real MCP client session
against the running aggregator): version discovery/resolution against the
real upstream repo (including two real bugs found and fixed along the way
— transitive-ancestor major/minor leakage, and `-vNext` preview builds
outranking stable ones under naive "highest build wins"); an unscoped diff
rejected explicitly; a real symbol diff and a real multi-step symbol
history that correctly collapsed 2 raw touching commits down to 1 real
change; a genuinely new (country, version) build requested, queued, and
built for real through the actual build queue (not an ad hoc script),
confirmed via `bcatlas_version_status` and the promoted artifact on disk.

**Known open items, not yet fully closed:**
- ~~No trustworthy incremental-vs-cold wall-clock number has been
  captured yet~~ — now measured for real, twice, on both sides (see "Key
  facts already established" below): builds are fast (minutes), the
  shared serving daemon's cold reindex is genuinely slow (many hours on
  the hosted VM's 4-vCPU hardware), not the ~30min/~2hr originally
  assumed. This asymmetry is expected and fine — see that section for why.
- The hosted search daemon's stall-recovery watchdog killed the *wrong*
  process for an unknown amount of past time (fixed, not yet re-verified
  end to end): `daemon.pid` can go stale mid-run, pointing at an
  already-dead sibling from an earlier spawn attempt while the real,
  actively-computing daemon runs on as a completely untracked process —
  live-reproduced on the hosted VM. `_kill_shared_daemon_hard` now
  cross-checks via `/proc` instead of trusting the pidfile alone
  (`chunker/mcp_http_server.py`'s `_child_daemon_pids`), and the stall
  watchdog now also treats real CPU burn as forward progress
  (`_daemon_cpu_ticks`), not just the on-disk fingerprint/live counters —
  both signals were independently observed live to plateau for 300s+ on a
  genuinely healthy, actively-computing daemon. Whether this fully closes
  the failure mode (vs. just making it much rarer) isn't yet proven —
  needs another real stall to occur and be correctly recovered from, not
  just reasoned about.
- Corollary gap, not yet fixed: a daemon that dies mid-warmup (e.g. the
  client that triggered it disconnects) has no self-healing — nothing
  retries until the next real client request happens to come in. Live-
  reproduced this session: search was silently down for ~2 days because
  the one request that would have retried it never arrived. A periodic
  local health-check/keepalive would close this; proposed, not built.
- ~~Whether the shared system-wide `ccc` daemon's chunker resolution
  reliably finds `al_chunker`~~ — this was a real, two-part production
  outage, not a theoretical risk: `bcatlas_search` failed on the hosted
  default corpus, first with `ModuleNotFoundError: No module named
  'al_chunker'`, then (after a first PYTHONPATH-only fix that solved that
  half) with `... 'tree_sitter'` (al_chunker's own real dependency).
  `importlib.import_module` has no per-project `sys.path` insertion
  anywhere in cocoindex-code, and copying the file into a staging dir
  doesn't put that directory on `sys.path` either. Properly fixed by
  passing `--with-editable <chunker dir>` to every `uv run` that spawns a
  `ccc`/daemon subprocess (`scripts/start-search-server.sh`,
  `build/build/incremental.py`'s `_run_ccc_index`) — builds an ephemeral
  overlay venv merging cocoindex-code's own locked deps with chunker/'s
  (bc-al-chunker) real ones, which the daemon these subprocesses spawn
  inherits too (it locates its own `ccc` executable via
  `Path(sys.executable).parent`).
- Reindex-webhook wiring into the sandbox-history repo's own GitHub
  Actions is still not built — tracked as future work, the build/serve
  split it would wire into now exists.

## Key facts already established — don't re-derive

See the constitution's "Technology & Data Constraints" section for the
durable architectural facts (storage reality, tool choices, corpus
topology). A few additional facts from the design process worth keeping
here since they're not principles, just measurements:

- Same-country version hops are cheap: a full 99-build span on `w1-28`
  (`w1-28.1.49838.50848` → `w1-28.2.50931.52151`, i.e. exactly a "28.1 vs
  28.2" comparison) touched 269 of the corpus's `.al` files — roughly 1%.
- `bcatlas_request_version` builds land well inside the target (10-30 min)
  SLA — measured live, twice, re-running the same two real incremental
  builds before/after a fix: a 75-changed-file hop went 332s → 176s, a
  1897-changed-file hop went 863s → 409s. The fix
  (`graphify update --no-report`, `build/build/incremental.py`'s
  `_run_graphify_update`) skips `graphify-al`'s report-only computations
  (`score_all`/`god_nodes`/`surprising_connections`/`suggest_questions`/
  `generate`) — cProfile showed these dominating up to 90% of build
  wall-clock via a full-graph `betweenness_centrality` call, purely to
  populate a `GRAPH_REPORT.md` section nothing programmatic reads (the
  equivalent served MCP tools recompute live from the graph on query
  instead). Community detection (`cluster`) itself still runs every build
  — that's real, necessary work feeding `graph.json`.
- The shared serving daemon's cold reindex is genuinely slow — much
  slower than either the original ~30min estimate or the "well past 2
  hours" figure from earlier this session, and this is now measured with
  real IndexingProgress counters, not inferred from DB file size: ~39% of
  ~24,300 files done after ~6 hours, then ~55% after ~14 hours (a second
  independent sample), on the hosted VM's 4-vCPU hardware. Don't assume
  this number is stable across VM/corpus changes — re-measure via
  `project_status()`'s live counters (`num_unchanged + num_reprocesses` vs.
  `total_files`) rather than inferring from `target_sqlite.db`'s byte size,
  which was observed live to plateau for very long stretches during
  genuine, healthy compute.
  **Correction (2026-08-03), see constitution Principle VIII**: the
  earlier claim here that "a fresh daemon process cannot trust ANY prior
  state at all" was wrong for this always-warm shared serving daemon
  specifically (it remains true of a brand-new per-(country, version)
  build artifact's *first* build, which genuinely has no prior state).
  Verified directly against the deployed `cocoindex-code` source
  (`tools/cocoindex-code/src/cocoindex_code/project.py`'s `Project.create`
  + `resolve_db_dir()`/`settings.py`): the daemon's `.cocoindex_code/`
  state (`cocoindex.db`, `target_sqlite.db`) lives at a deterministic,
  disk-backed path that a fresh process reopens rather than recreates, and
  `self._app.update()` is a content-hash-based incremental engine that
  skips already-indexed files as `num_unchanged`. Live-confirmed: a
  routine `systemctl restart` on the VM resumed with `num_unchanged` already
  >10,000, not 0. **This means routine deploys do NOT have to cost a full
  cold reindex** — the daemon survives a restart cheaply as long as its
  on-disk `.cocoindex_code/` state isn't wiped and the project_root path
  stays the same. The one open risk, not yet stress-tested: whether this
  holds under an *ungraceful* kill (the stall watchdog's SIGKILL path),
  vs. only the graceful SIGTERM restart path actually observed live so
  far.
- Cross-country content overlap is much higher than git ancestry suggests:
  `w1-28` vs `us-28` share ~87% byte-identical `.al` files at the same
  path (10,962 of 12,604 in `us-28`) despite the two branches having no
  shared commit ancestry at all (`ahead_by`/`behind_by` ≈ 4,000 each way).
  Always measure real tree content diffs for this kind of question — see
  constitution Principle V.
- Two concrete test scenarios validated the original PoC and remain the
  bar for regressions: **"add custom validation right before a sales order
  posts"** (tests whether multiple plausible `OnBefore*` candidates come
  back with enough context to disambiguate, not a forced single answer)
  and **"make an outbound REST call from AL"** (tests the docs+code
  combined index). See `REPORT.md` for the full transcripts.
- Microsoft's own tooling (`altool launchmcpserver`/`launchlspserver`,
  Event Recorder) was evaluated and set aside — LSP is per-workspace not a
  natural fit for a shared static index, the MCP server is a
  build/diagnostics tool not a connectivity/search tool, Event Recorder is
  dynamic/runtime discovery, complementary but out of scope for a static
  index. Don't re-evaluate unless the two layers above prove insufficient.

## Non-goals (current)

- Don't try to fix or extend `graphify-al`'s partial call-resolution
  (event-driven/interface dispatch isn't followed statically) — documented
  upstream limitation, not a bug to chase (constitution Principle VI).
- Reindex-webhook wiring into the sandbox-history repo's own GitHub Actions
  is not yet built — the build/serve split it would wire into now exists
  (see "Known open items" above), still tracked as future work, not
  excluded forever.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at specs/007-file-watcher-reindex/plan.md
<!-- SPECKIT END -->
