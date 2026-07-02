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

**Built and running** (local, single-version — `w1-28` only, still the
current runtime reality, the multi-country/multi-version serving layer
described below is not built yet):
- `w1-28` AL source + two docs corpora (`dynamics365smb-docs`
  business-central docs, `dynamics365smb-devitpro-pb` AL developer/compiler
  reference) indexed via a custom `tree-sitter-al` chunker into
  `cocoindex-code`, served as MCP over HTTP (`chunker/`).
- The structural graph extracted via the `graphify-al` fork, served as MCP
  over HTTP (`tools/graphify-al`), including on-demand exact-source lookup
  tools (`get_signature`, `get_procedure_body`, `get_object_source`) that
  re-parse real source on request rather than caching text in the graph.
- A thin aggregator (`aggregator/`) presenting one `/mcp` endpoint to
  clients, forwarding to both backends.
- Real tester validation against both original test scenarios plus organic
  use; see `REPORT.md` for the full account and `README.md` for the
  current architecture diagram and local Quick Start.

**Being designed now, not yet built** — the real-implementation phase:
serving an unbounded set of (country, version) pairs concurrently, bounded
only by hardware (constitution Principle IV). This decomposes into
independent pieces, none started yet:
1. A version resolver: `(country, version-spec) → exact commit`, tolerant
   of exact builds or loose specs ("latest 28.1").
2. On-demand historical commit/blob fetch (today's submodules only have
   each branch's tip locally).
3. A version/country discovery tool so a calling agent — which has no way
   to know what's available — can list real options before requesting one.
4. A diff tool scoped to a file or, better, a resolved symbol (reusing the
   `get_procedure_body`-style tree-sitter extraction against two arbitrary
   historical blobs instead of the working tree) — never an unscoped
   whole-repo diff, that's already been measured as unusably large.
5. A multi-step "how did this procedure change across these versions"
   chain, built from (4) plus `git log` scoped to the containing file.
6. The build/serve split itself (constitution Principle II): a bounded,
   GPU-aware build queue writing to staging + atomic promote, and a
   multi-tenant serving layer with one shared embedding-capable process
   instead of one process per warm version.
7. Clone-then-patch incremental builds for version/country hops, using
   cocoindex-code's own stock incremental `index()` against a cloned
   project directory with only the git-diffed files swapped in — no fork
   of cocoindex needed for this.

Each of these should go through its own `/speckit-specify` cycle rather
than being designed further in this file.

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
  is not yet built — tracked as future work once the build/serve split
  above exists to wire it into, not excluded forever.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at specs/001-multi-version-serving/plan.md
<!-- SPECKIT END -->
