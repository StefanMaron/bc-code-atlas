# CLAUDE.md — bc-code-atlas

## Mission

Build a local proof-of-concept for a hosted, MCP-queryable code+docs index of Microsoft Dynamics 365 Business Central (AL language), then validate whether it's good enough to eventually expose as a public community service. This PoC must run entirely on this machine, but must be wired exactly like the real remote deployment will be: MCP servers over HTTP on localhost, with a separate Claude Code session/config connecting to them as if they were remote — not in-process function calls, not stdio-only shortcuts. The whole point is to catch integration/serving problems now, before anything is exposed publicly.

You are a fresh agent with no memory of the conversation that produced this brief. Everything you need is below. Ask the user (via chat, not by guessing) if something here is ambiguous enough to block a real decision — otherwise use judgment and proceed.

## Why this exists (background)

Business Central's AL source is tracked in full, per-version, in `github.com/StefanMaron/MSDyn365BC.Sandbox.Code.History` (branches like `w1-28` hold the extracted `.al` source for World-Wide base app version 28's latest build; the `main` branch only has automation scripts, ignore it). The long-term goal is a public MCP server that lets any BC/AL developer's coding agent query "how is the codebase connected" and "how do I do X" against real Microsoft source — semantically (natural language → relevant code/docs) and structurally (exact call/subscribe/extend graph).

Extensive research (see "Prior research" below) converged on a **hybrid two-layer architecture** — no single tool does both halves well, and that's by design, not a compromise:

1. **Semantic layer** (fuzzy, natural-language retrieval over code + docs) — build this on **`cocoindex-io/cocoindex-code`** (github.com/cocoindex-io/cocoindex-code), a mature (2.3k★, 44 releases) CLI/MCP wrapper around the CocoIndex Rust indexing engine. It already ships: an MCP server (`search` tool with query/limit/offset/language/path filters), a Claude Code Skill, pluggable embedding backends via LiteLLM (use a local Ollama model so this stays free and self-hostable), and — critically — **pluggable custom chunkers registered in project settings**. AL is not a supported language out of the box, so the one piece of real custom work is writing an AL-aware chunker plugin.

2. **Structural layer** (exact relationship graph: `subscribes`, `extends`, `calls`) — build this on **`ChristianHovenbitzer/graphify-al`** (github.com/ChristianHovenbitzer/graphify-al, branch `al-support`), a fork of `safishamsi/graphify` with AL support already added (objects, procedures, cross-object calls, event subscriptions, extension targets — see its `AL_SUPPORT.md`). Known caveat: cross-object `calls` resolution is "partial — only direct, statically-typed calls resolve"; event-driven/interface dispatch isn't followed statically. Also worth comparing against the user's own **`StefanMaron/AL-Dependency-MCP-Server`** (github.com/StefanMaron/AL-Dependency-MCP-Server), which parses *compiled* `.app` symbol packages rather than source text — potentially more accurate reference tracking, but needs actual `.app`/`.alpackages` files (not present in the sandbox-history repo, which only commits extracted `.al` source — you'll need to obtain these separately, e.g. via `bccontainerhelper`'s `Get-BCArtifactUrl`/`Download-Artifacts`, or by compiling).

3. **AL chunk-boundary parsing**, wherever it's needed (semantic chunker, and/or structural extraction if you go beyond the two forks above), should use **`SShadowS/tree-sitter-al`** (github.com/SShadowS/tree-sitter-al) — a mature, standalone AL grammar (100% parse success on 15,358 real production AL files, ships Python/Node/WASM bindings). Don't write your own AL parser.

4. **Docs**: Microsoft's public BC developer docs live at `github.com/MicrosoftDocs/dynamics365smb-devitpro` (the `dev-itpro` folder, markdown). Index this alongside the AL source in the *same* semantic index so queries return both matching doc pages and matching code in one ranked result set.

## Prior research — key facts already established, don't re-derive

- Latest stable World-Wide branch in the sandbox-history repo is **`w1-28`**.
- `cocoindex-code`'s real flow API (verified against actual docs) looks like this for a generic language:
  ```python
  data_scope["files"] = flow_builder.add_source(
      cocoindex.sources.LocalFile(path=..., included_patterns=["*.py", ...]))
  file["chunks"] = file["content"].transform(
      cocoindex.functions.SplitRecursively(), language=file["extension"], chunk_size=1000, chunk_overlap=300)
  @cocoindex.transform_flow()
  def code_to_embedding(text): return text.transform(cocoindex.functions.SentenceTransformerEmbed(model=...))
  code_embeddings.collect(filename=..., location=chunk["location"], code=chunk["text"], embedding=chunk["embedding"])
  code_embeddings.export("code_embeddings", cocoindex.storages.Postgres(), primary_key_fields=[...], vector_indexes=[...])
  ```
  For `.al`, `SplitRecursively(language=".al")` will silently fall back to naive recursive text splitting — it has no idea what an AL object/procedure/attribute is. That's exactly the gap the custom chunker plugin must close: parse with `tree-sitter-al`, yield one chunk per procedure (or per object for short ones), and attach metadata fields — `object_type`, `object_name`, `procedure_name`, `attributes` (e.g. `IntegrationEvent`, `EventSubscriber`) — not just bare text.
- `cocoindex-code` storage is LMDB (vectors) + SQLite (metadata) by default, not Postgres — confirm this still fits a single shared home-hosted service with concurrent readers during a reindex (LMDB supports multiple readers + one writer natively, which should be fine, but verify empirically rather than assuming).
- Two concrete test scenarios were identified as the bar this PoC must clear:
  1. **"I want to add custom validation right before a sales order gets posted."** Business Central's posting codeunit(s) have multiple plausible "OnBefore*" events (e.g. before validation checks vs. right before the actual post/commit) — a good answer requires more than top-1 similarity: retrieve several real candidates with full surrounding code context (not isolated one-liners) so the differences in execution order and available parameters are visible, and let the agent's own reasoning pick (or ask a clarifying question) — don't expect the tool to auto-resolve to a single "correct" event.
  2. **"How do I make an outbound REST call from AL?"** — should surface System App's HTTP-related objects *and* real call-site examples elsewhere in the base app, plus relevant doc pages — this tests the docs+code combined index, and is expected to work better than the events scenario since usage-by-example is less ambiguous than event selection.
- Microsoft's own official tooling (`altool launchmcpserver`, `altool launchlspserver`) was evaluated and set aside for this specific project: the LSP is compiler-accurate but per-workspace/session, not a natural fit for a shared static index; the MCP server is a build/compile/diagnostics tool, not a connectivity/search tool. Don't re-evaluate these unless the two layers above prove insufficient.
- Microsoft's "Event Recorder" is a *dynamic/runtime* discovery tool (record events fired during a live scenario in an actual running BC session) — out of scope for this static-index PoC, but note it in the final report as a complementary tool for later, especially for the event-ordering disambiguation problem.

## What "done" looks like for this PoC

1. A working local setup, entirely on this machine:
   - `w1-28` AL source checked out locally (shallow clone of `StefanMaron/MSDyn365BC.Sandbox.Code.History`, branch `w1-28`).
   - `dynamics365smb-devitpro` docs repo checked out locally (or just the `dev-itpro` subtree).
   - A custom AL chunker plugin for `cocoindex-code`, built on `tree-sitter-al`, registered and indexing the `w1-28` source with per-procedure chunks carrying the metadata fields above.
   - The docs repo indexed into the same `cocoindex-code` project/collection (or a documented reason why it had to be a separate collection with merged query results instead).
   - `cocoindex-code`'s MCP server running with **HTTP transport** (not stdio) on a local port.
   - `graphify-al` (or `AL-Dependency-MCP-Server`, your call after comparing them — see "Open decision" below) extracting the structural graph for `w1-28` and serving it via its own MCP server, also over HTTP, on a different local port.
2. A **separate** local Claude Code project/config (simulate the "external developer" side) with an `.mcp.json` (or equivalent) pointing at `http://localhost:<port1>/mcp` and `http://localhost:<port2>/mcp` — i.e., configured the same way a remote community user would point at your eventual Cloudflare-tunneled endpoints, just pointed at `localhost` instead of a public hostname.
3. Both test scenarios above run for real, from that separate Claude Code session, against the live MCP servers — not simulated, not read from source by the agent directly (that would defeat the point of testing the actual served experience).
4. A short written report (`REPORT.md` in this repo) covering:
   - Setup friction encountered (what took longer/was harder than expected, especially the custom chunker plugin).
   - Indexing time and resource usage for `w1-28` + docs at full scale.
   - Full transcripts or summaries of both test-scenario runs, with an honest verdict on quality (did it surface the right candidates with enough context to disambiguate? did the docs+code combination actually help?).
   - Whether LMDB storage held up fine with the HTTP MCP server under point 2's concurrent access pattern.
   - A go/no-go recommendation, and if "go," what's still missing before this could be exposed publicly (auth, rate limiting, uptime expectations, reindex-on-webhook wiring from the sandbox-history repo's daily automation — that wiring itself is explicitly **out of scope** for this PoC, just note what it'll need).

## Open decision — resolve empirically, don't guess

Compare `graphify-al` vs. `StefanMaron/AL-Dependency-MCP-Server` for the structural layer by running both against the same real `w1-28` data if feasible, and pick based on which gives more accurate/complete edges for the test scenarios — not based on which is more convenient to set up. Document why in the report.

## Explicit non-goals for this PoC

- No public hosting, no Cloudflare Tunnel, no auth/rate-limiting — purely local.
- No reindex-webhook wiring into the sandbox-history repo's GitHub Actions — note what it would need, don't build it.
- No multi-branch/multi-country support — `w1-28` only, this time for real (a prior PoC attempt scoped multiple branches and that's explicitly not the goal here).
- Don't try to fix or extend `graphify-al`'s partial call-resolution — note the limitation, work around it in the report's conclusions if it matters for the test scenarios, don't patch the fork's internals unless it's a trivial, obviously-correct fix.
