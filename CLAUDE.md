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
- No trustworthy incremental-vs-cold wall-clock number has been captured
  yet (constitution Principle V — don't assert one without measuring it
  for real; two manual attempts were contaminated by tooling/process
  collisions and discarded rather than reported).
- Whether the shared system-wide `ccc` daemon's chunker resolution
  (`importlib.import_module`, no per-project `sys.path` insertion in
  cocoindex-code) reliably finds `al_chunker` for every brand-new staging
  project is defensively mitigated (the chunker is copied into each
  staging dir) but not proven across many builds yet.
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
at specs/001-multi-version-serving/plan.md
<!-- SPECKIT END -->
