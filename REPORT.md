# BC Code Atlas — PoC Report

Local proof-of-concept for a hybrid semantic + structural MCP-queryable index over
Microsoft Dynamics 365 Business Central AL source (`w1-28`) and developer docs, wired
as two independent MCP servers over HTTP — exactly as a real remote deployment would
be, just pointed at `localhost` instead of a public hostname. See [CLAUDE.md](CLAUDE.md)
for the original brief.

**Status note**: this report documents the single-version local PoC phase and its
"go" recommendation, preserved as-is for the historical record. The project has
since moved into building the real multi-country, multi-version public service —
see [CLAUDE.md](CLAUDE.md) for current status and
[.specify/memory/constitution.md](.specify/memory/constitution.md) for the
governing architecture principles that phase established.

## TL;DR — Go / No-Go

**Go**, with caveats. Both layers work, both test scenarios produced real, accurate,
well-grounded answers from a genuinely separate Claude Code session talking to the
indexes only over HTTP MCP — not simulated. The structural layer (`graphify-al`) had
a real bug dropping ~98.6% of cross-object call edges and shipping with `uses`
(data-dependency) edges off by default — both fixed and verified post-report, see
"Post-PoC hands-on findings" below. The semantic layer (`cocoindex-code` + custom AL
chunker) had a measurable docs-vs-code ranking gap (a "modality gap" from the
general-purpose embedding model), substantially closed by an embedding-model swap and
full re-index, verified against the live served MCP path — see the same section.
See "Go/no-go detail" at the end for the full punch list.

## Architecture as built

```
w1-28 AL source (17,161 files) ─┐
                                 ├─► cocoindex-code (custom AL chunker, tree-sitter-al)
BC docs, business-central/ (2,631 .md) ─┘        │
                                                   ▼
                                    SQLite (sqlite-vec) + LMDB (engine state)
                                                   │
                                    HTTP MCP wrapper (chunker/mcp_http_server.py)
                                                   │
                                        http://localhost:8801/mcp
                                                   │
w1-28 AL source ──► graphify-al (tree-sitter-al) ──► graph.json (266,728 nodes / 589,170 edges)
                                                   │
                                    graphify's own HTTP MCP server (built-in)
                                                   │
                                        http://localhost:8802/mcp
                                                   │
                              client-session/.mcp.json (separate Claude Code project)
                                                   │
                                   `claude -p` headless session, MCP-only tool access
```

## Setup friction (the point of this PoC)

1. **The docs repo brief assumed a `dev-itpro` folder that no longer exists.**
   `MicrosoftDocs/dynamics365smb-devitpro` also doesn't exist under that name — the
   real repo is `MicrosoftDocs/dynamics365smb-docs`, and its content has been
   reorganized into one flat `business-central/` folder (2,631 `.md` files) mixing
   admin/functional/developer docs with no dev-only subtree. Indexed the whole folder
   as-is. Net effect, confirmed independently by the scenario 2 test run itself: there
   is currently no true developer-language-reference content (e.g. `HttpClient` API
   docs) in the index — only functional/admin docs. This is a real gap for the
   docs+code combined-index value proposition, not a PoC shortcut.

2. **`cocoindex-code`'s MCP server is stdio-only; HTTP is not a CLI flag.**
   `ccc mcp` hardcodes `run_stdio_async()`. The underlying `mcp==1.26.0` SDK object
   returned by `create_mcp_server()` does support `run_streamable_http_async()`
   (default path `/mcp`, configurable host/port) — it's just never wired up in the
   CLI. Wrote a ~35-line wrapper, [chunker/mcp_http_server.py](chunker/mcp_http_server.py),
   that imports the same factory and serves it over Streamable HTTP instead. No fork
   of `cocoindex-code` needed.

3. **`Chunk` has no metadata columns — AL structure had to be encoded into chunk text.**
   `cocoindex-code`'s `Chunk`/`CodeChunk` schema only carries `text` + line/byte
   position + language — no fields for object type/name, procedure name, or
   attributes. The AL chunker ([chunker/al_chunker.py](chunker/al_chunker.py)) works
   around this by prepending a synthetic header line to every chunk, e.g.:
   ```
   -- object_type: codeunit | object_name: "Sales-Post" | procedure: RunWithCheck
   ```
   This reaches the embedding model and shows up verbatim in search results — good
   enough for this PoC, but a real deployment would want first-class metadata columns
   (filterable by object type, etc.) rather than baking it into embedded text.

4. **A handful of real AL procedures are 100K+ characters and broke the embedding
   backend outright.** The first full-corpus index run failed with hundreds of
   `litellm.APIConnectionError: ... 400 Bad Request` errors from Ollama's `/api/embed`
   endpoint. Root-caused via bisection: some AL procedures (deeply nested case/if
   logic, or — in one case — a file using AL's `#if`/`#else` preprocessor
   conditional-compilation feature, which `tree-sitter-al` wraps in a
   `preproc_split_declaration` node the chunker didn't originally recognize as an
   object) produce single chunks far beyond `nomic-embed-text`'s ~2048-token context
   window. Fixed with a hard byte-window sub-splitter (1,500 chars, 150-char overlap)
   applied to any chunk over 6,000 characters, verified afterward that the max chunk
   size across the full 17,161-file corpus dropped from 124,769 to 6,254 chars.

5. **The sandbox environment itself restarted mid-index**, killing the tracked
   foreground `ccc index` process and leaving **two duplicate daemon processes**
   racing over the same SQLite file (one from before the restart, one auto-spawned
   after) — producing a burst of real `sqlite3.OperationalError: database is locked`
   errors. Not a cocoindex-code bug per se, but a real operational lesson: the daemon
   doesn't detect or refuse a second instance over the same DB, so a bad restart can
   leave a project silently double-writing. Fixed by force-killing both stale
   daemons and resuming — cocoindex-code's indexing is properly incremental (it
   correctly reported "252,312 chunks unchanged" style status and picked up cleanly
   with zero data loss on resume).

6. **The structural-layer open decision resolved itself empirically, fast.**
   `StefanMaron/AL-Dependency-MCP-Server` is confirmed, at the source level, to
   require compiled `.app`/`.alpackages` symbol files — `includePatterns =
   ['**/*.app']` is hardcoded, and it unzips the `.app` (BC's package format is a ZIP
   with a 40-byte NAVX header) to read `SymbolReference.json`. We only have raw `.al`
   text, so this tool was a non-starter for this pass — no time wasted forcing it.
   `graphify-al` (branch `al-support`) ran the full 17,161-file corpus in ~4–4.5
   minutes with zero crashes, producing 266,728 nodes / 589,170 edges, and — bonus —
   **already ships its own MCP server with Streamable HTTP transport**
   (`python -m graphify.serve graph.json --transport http --port 8802`), so no wrapper
   was needed there at all, unlike the semantic layer.

7. **`graphify-al` has a real, reproducible cross-object call-resolution bug.**
   Its own `AL_SUPPORT.md` documents cross-object calls as "partial — only direct,
   statically-typed calls resolve." In practice it's worse than that phrasing
   suggests: **only 3,926 of 285,737 (`1.4%`) `calls` edges in the full corpus are
   cross-object** — the rest are intra-object. Root cause (confirmed against a live
   tree-sitter-al parse): `graphify/extract.py`'s `collect_vars()` scans
   `var_section.children` directly for `variable_declaration` nodes, but the grammar
   actually nests them one level deeper (`var_section → var_body →
   variable_declaration`). Since the dominant BC pattern is exactly
   `var MyImplCodeunit: Codeunit "X"; ... MyImplCodeunit.DoThing()`, this silently
   drops the vast majority of the "impl codeunit" call chains that matter most for
   understanding real base-app control flow. This looks like a genuinely trivial,
   one-line, obviously-correct fix (recurse into `var_body`) — per the brief's
   instruction not to patch fork internals unless the fix is trivial and
   obviously-correct, **this is a judgment call worth flagging back to you rather than
   deciding unilaterally**; I did not apply it. `subscribes` and `extends` edges (once
   `extends` results are filtered to exclude `app.json`-sourced noise, ~70% of raw
   `extends` edges in one sampled folder were manifest feature-flags, not real AL
   extension relationships) were independently spot-checked against real source and
   found correct.

8. **Storage is SQLite + LMDB, not Postgres — corrects a prior-research assumption.**
   The queryable vector index is SQLite via the `sqlite-vec` extension
   (`target_sqlite.db`, ~1.1 GB at full scale). A separate LMDB-backed file
   (`cocoindex.db`) holds only CocoIndex's own internal incremental-processing state,
   not queryable chunk data. Nothing in `cocoindex-code` supports Postgres; that
   option exists in the underlying `cocoindex` library in general but isn't wired up
   by this wrapper.

## Indexing time & resource usage at full scale

| | |
|---|---|
| AL source | 17,161 files, 510 MB (`w1-28`, `MSDyn365BC.Sandbox.Code.History`) |
| Docs | 2,631 `.md` files, 224 MB (`business-central/` folder — see friction #1) |
| AL chunks produced | 233,591 (custom tree-sitter-al chunker, capped at 6,254 chars max) |
| Doc chunks produced | 18,721 (built-in recursive splitter) |
| **Total indexed chunks** | **252,312 across 19,913 files, 0 errors** (after the two fixes above) |
| Embedding model | `ollama/nomic-embed-text` via LiteLLM, 768-dim, GPU (RTX 4080) |
| Vector DB size | 1.1 GB (`target_sqlite.db`) |
| Structural graph | 266,728 nodes / 589,170 edges, `graph.json` 318 MB, built in **~4–4.5 min** |

Indexing wall-clock for the semantic layer isn't a single clean number because of the
mid-run environment restart (friction #5) — but cocoindex-code's incremental design
meant the interruption cost no reprocessing, only a restart of the daemon. Both HTTP
MCP servers stayed resource-light at rest (~370 MB combined RSS).

## Concurrency: does the storage hold up under a live HTTP MCP server?

Partially validated, with a real caveat. Running `ccc status` (a read query against
`target_sqlite.db`) **concurrently with an active `ccc index` write** produced a
cluster of `sqlite3.OperationalError: database is locked` errors in the daemon log —
this happened organically during the interruption/recovery in friction #5, not as a
deliberately engineered stress test, but it's real signal: **SQLite in its default
(non-WAL) journal mode does not give the "supports many concurrent readers"
tolerance LMDB would.** The actual serving path (search queries via the HTTP MCP
server) goes through the daemon's own single-process request serialization, which
likely masks this for read-only query traffic — but a background reindex running
concurrently with live query traffic (the CLAUDE.md's actual concern — is a shared,
always-on service like this, real answer: **not yet validated as safe**, and
`PRAGMA journal_mode=WAL` on `target_sqlite.db` should be checked/set before treating
this as a solved problem for a shared service.

## Test scenario 1: "custom validation right before a sales order gets posted"

Run from the separate `client-session/` Claude Code project (`.mcp.json` → HTTP only,
`--allowedTools` scoped to just the two MCP servers' tools, no filesystem/direct-source
access) via `claude -p`. 44 tool-use turns, ~8.5 minutes, real MCP round-trips only.

**Result** (full transcript in `client-session/scenario1_output.json`): correctly
found and distinguished two real candidate events in `Codeunit 80 "Sales-Post"`
(`Base Application/Sales/Posting/SalesPost.Codeunit.al`):

- `OnBeforePostSalesDoc` — fires at the very first line of `RunWithCheck`, before any
  checks/writes; recommended as the standard hook for blocking validation.
- `OnBeforePostCommitSalesDoc` — a late-stage hook right before the final `Commit()`,
  useful only if validation needs to inspect posting results first.

It quoted real source lines, correctly explained execution order and why failing
fast at the first hook is cheaper, gave a clear recommendation (`OnBeforePostSalesDoc`)
rather than punting the question back, and — importantly — **was honest about a
verification gap**: it couldn't pull the exact call-site line for the second event
through search and said so explicitly instead of guessing. This is exactly the
"several real candidates with full context, let the agent's reasoning pick" bar the
brief set, met.

## Test scenario 2: "outbound REST call from AL"

26 tool-use turns, ~4.7 minutes.

**Result** (full transcript in `client-session/scenario2_output.json`): correctly
identified the real AL HTTP type set (`HttpClient`/`HttpRequestMessage`/
`HttpResponseMessage`/`HttpContent`/`HttpHeaders`), correctly noted that the newer
`System.RestClient` codeunit **does not exist in this w1-28 build** (it checked rather
than assumed), and surfaced a real, complete call-site example —
`codeunit 4508 "Email - Outlook API Client"` sending mail via Microsoft Graph — with
exact file/line references, the `IsBlockedByEnvironment()` gotcha most real AL
extensions hit, and three other real cross-cutting usages (`E-Document Core`,
`SignUp Authentication`, Azure File/Blob Services helpers). It also **independently
rediscovered friction #1** (no dev-itpro developer reference docs in the index) and
said so plainly instead of fabricating a docs citation. Usage-by-example, as
predicted in the brief, worked better than the ambiguous-event scenario — no
disambiguation was needed, just accurate retrieval.

## Post-PoC hands-on findings (real usage, not scripted scenarios)

After the initial pass above, the PoC was pointed at a second, real personal repo
(`MyFirstSampleApp`) so it could be tried interactively rather than only through the
two scripted scenarios. That surfaced two more real, load-bearing findings:

1. **`graphify-al`'s `var_body` call-resolution bug (friction #7 above) is fixed,
   verified, and shipped as [PR #1 upstream](https://github.com/ChristianHovenbitzer/graphify-al/pull/1)**,
   with 3 new regression tests and a passing run of 458 pre-existing tests. Applied
   the same fix to our local running copy immediately rather than waiting on the
   upstream merge, since it's the same one-line change. Real effect on the full
   `w1-28` corpus: cross-object `calls` edges went from **3,926/285,737 (1.4%)** to
   **250,197/532,040 (47.0%)** — the graph now actually reflects how BC's codeunits
   call each other, instead of only seeing intra-object calls.

2. **`graphify-al`'s `uses`/`references` relation (Record/Page/Report data
   dependencies) is off by default**, gated behind an undocumented
   `GRAPHIFY_AL_USES=1` env var, with this reasoning in the source: *"high-volume and
   tends to bury the call graph under a table-sharing hairball."* This directly
   explained a real, reproducible gap: searching from a business concept like
   `Customer` found only the `Customer` table's own CRUD procedures, never the real
   posting logic (`Gen. Jnl.-Post Line`, which does reference `Customer` as a `var`)
   — because with `uses` off, **no edge connects them at all**. Measured the actual
   "hairball" risk before enabling it permanently: +23,298 edges (~4%) with `uses`
   alone, 1,888 unique reference targets with a **median in-degree of 3** — a small
   number of genuinely central tables (`Item` 823, `Sales Header` 719, `Customer` 426)
   are real hubs, matching the actual domain, not noise. Enabled permanently
   (`GRAPHIFY_AL_USES=1`) on both the base-app and sample-app graphs; combined with
   fix #1 above, `references` edges reached 61,554 on the full corpus.

3. **Semantic search has a measurable "modality gap" between docs and code that a
   general-purpose embedding model does not close.** Same live index, same query
   ("customer posting logic"), embedded with the same `nomic-embed-text` model used
   in production:
   ```
   query vs REAL CODE chunk (GenJnlPostLine):  0.453 cosine similarity
   query vs generic DOC sentence:               0.582 cosine similarity
   ```
   Natural-language phrasing embeds reliably closer to natural-language docs than to
   dense AL syntax, independent of true relevance — this is why generic queries kept
   surfacing docs over the actually-relevant codeunit. **Not yet fixed** — the two
   live options are (a) a code-tuned embedding model instead of `nomic-embed-text`
   (re-embedding cost: ~250K chunks), or (b) a lexical/FTS layer (e.g. SQLite FTS5
   alongside the existing `sqlite-vec` table) merged with the semantic results so
   literal identifier terms always surface exact matches. Deliberately rejected a
   hand-curated "business concept → canonical object" mapping as the fix, since it
   doesn't generalize — the two options above do.

4. **Added generic (not curated) `TableRelation` foreign-key extraction to the
   structural graph — a real, verified fix for the same `Customer` bridging problem,
   sourced from an evaluation of `SShadowS/graphify`'s unmerged `al-language-support`
   branch** (Torben Leth, the `tree-sitter-al` author's own fork of `graphify`). That
   branch's feature list was excellent — property-driven refs, `TableRelation` FKs,
   trigger taxonomy, event pub/sub unification, obsolete metadata, 54 dedicated AL
   tests — but hands-on testing found it was written against an assumed grammar
   shape and never verified against a real parse: 43/205 of its own AL tests failed,
   including foundational ones (`extract_al` on a basic fixture produced *zero*
   procedure nodes and *zero* `calls` edges). Root cause: `_handle_object_declaration`
   iterated `obj_node.children` directly, but the real grammar nests everything
   inside a `declaration_body` field — not adopted wholesale as a result.

   Instead, cherry-picked just `TableRelation` extraction, rewritten against the
   *verified* real grammar shape (`fields_section` -[`body`]-> `fields_body` ->
   `field_declaration` -[`body`]-> `declaration_body` -> `property`, confirmed via a
   live parse, not assumed) and added directly to our already-working, already-fixed
   fork. Found and fixed two more real bugs in the process before trusting it:
   - The naive per-fact dedup key `(src, tgt, rel)` silently dropped legitimate
     distinct relationships — e.g. `SalesHeader`'s `Sell-to Customer No.` and
     `Bill-to Name` both point at `Customer`, so the second field's edge was lost.
   - More fundamentally: `graphify`'s default graph mode is a **simple graph**
     (one edge per `(source, target)` pair) — `MultiDiGraph` support exists only as
     an internal capability probe with, per its own code comment, *"no call sites
     added yet."* Confirmed this doesn't just affect field dedup: verified `extract()`
     directly (bypassing the CLI) to isolate the loss, since parallel table_relation
     facts to the same target can never survive as separate edges regardless of the
     dedup key. Fixed by merging same-`(src, tgt)` facts into one edge carrying a
     `fields: [...]` list instead of relying on multiple edges to survive.

   Verified against real data: `SalesHeader → Customer` now carries
   `fields: ["Sell-to Customer No.", "Bill-to Customer No.", "Bill-to Name",
   "Sell-to Customer Name"]` on one edge. On the full corpus: **7,950 `table_relation`
   edges**, of which **176 distinct real objects** have a genuine foreign-key edge to
   `Customer` — exactly the generic (non-curated) concept-bridging the earlier
   `uses`/`references` fix (finding #2) didn't fully cover, since `TableRelation` FKs
   are declared on fields regardless of whether the referencing object ever
   instantiates a `var Customer: Record Customer` itself. All 2,245 pre-existing
   `graphify-al` tests still pass. Deployed to both the base-app and sample-app graph
   servers. Not sent upstream (unlike finding #1) — this is a new feature addition
   scoped specifically to this project's needs, not a bug fix to an existing one.

5. **Closed the search-side modality gap (finding #3) with the "proper fix": swapped
   the embedding model to `ibm-granite/granite-embedding-97m-multilingual-r2` and did
   a full re-index of the ~250K-chunk corpus** (deliberately rejected the "quick"
   lexical/FTS bolt-on — a model swap fixes the root cause instead of papering over
   it with a second ranking signal). Chose Granite over the initially-preferred
   `lightonai/LateOn-Code-edge` because the latter requires `trust_remote_code=True`
   (executes third-party Python from the model repo) — a materially different trust
   decision than loading ordinary model weights, so a safer code-aware alternative
   was used instead.

   Full re-index reached **247,821 chunks across 19,913 files** (229,100 AL + 18,721
   markdown). The run needed several resumes — twice from sandbox/session restarts,
   and once from a genuine daemon deadlock (two overlapping `ccc index` clients both
   blocked reading the same Unix socket after a restart raced a manual resume) — but
   `cocoindex-code`'s content-hash-based incremental indexing meant zero rework: each
   resume picked up exactly where the last one stopped, confirmed via chunk-count and
   per-process CPU-tick deltas rather than assumed.

   Verified the fix two ways, not just the raw embedding test:
   - **Same apples-to-apples pair as finding #3** (query `"customer posting logic"`
     vs. the real `PostCust` procedure in `Gen. Jnl.-Post Line` vs. a generic sentence
     from `finance-posting-groups.md`), re-embedded with Granite:
     ```
     query vs REAL CODE chunk (GenJnlPostLine.PostCust): 0.848 cosine similarity
     query vs generic DOC sentence:                       0.835 cosine similarity
     gap (doc − code): −0.012   (was +0.129 with nomic-embed-text)
     ```
     The gap didn't just shrink — for this canonical pair it **reversed**: code now
     outscores the doc sentence.
   - **The actual served path**, not just an isolated pair: same query through the
     live `search` MCP tool on the real base-app index (port 8801, HTTP transport,
     separate client session). Top 10 results: docs still take 6 of the top 10 slots
     including rank 1 (`finance-posting-groups.md`, score 0.891), but **code chunks
     now appear in the top 10** (`ERMCheckPostingGroups.Codeunit.al` at rank 6, score
     0.872; `CustomerDataMigrationFacade.Codeunit.al` at rank 8, score 0.871) — an
     0.019 top1-doc-vs-top-code gap, an order of magnitude smaller than the 0.129 raw
     baseline gap.

   Honest reconciliation of the two results: the isolated-pair test flips in code's
   favor, but the live full-corpus ranking still edges toward docs, because the real
   `dev-itpro` doc corpus has many near-duplicate sentences repeating the literal
   phrase "customer posting group(s)" verbatim across dozens of pages, which the
   individual-pair test can't capture — dense literal-term repetition across many doc
   chunks statistically outweighs one strong-but-singular code match in a top-10 vote,
   even when the model embeds that single code chunk closer for any individual query.
   **Net verdict: the proper fix substantially closed the modality gap and is a real,
   generalizable improvement — code is now competitive with docs instead of
   consistently losing — but full parity in a live multi-document ranking would need
   either a smaller embedding contribution per near-duplicate doc chunk (dedup/MMR at
   query time) or a still more code-tuned model.** Not further pursued in this PoC.

   Applied the same model + a from-scratch reindex to the tiny `MyFirstSampleApp`
   sample project too, for consistency with the base-app index (had to explicitly
   kill and restart the `cocoindex-code` daemon first — an in-place `rm` of its
   SQLite file was silently ineffective because the daemon still held the file open,
   so re-running `ccc index` just reconfirmed the stale nomic-embed-text vectors as
   "unchanged" instead of recomputing them under the new model).

6. **Hands-on testing after finding #5 surfaced a second, distinct problem that looked
   like more ranking noise but wasn't: for code-intent queries, `search` returned AL
   code, but the *wrong* code** — e.g. querying "insert customer ledger entry from
   general journal posting" returned real, on-topic hits, but every one of them was a
   **test codeunit** (`Tests-ERM`, `Tests-Dimension`, `Tests-Report`), never the actual
   `Gen. Jnl.-Post Line` → `PostCust` implementation, even though that procedure
   exists in the index and is exactly what "extend the base app" callers want.

   Root-caused before fixing anything, per the same "verify against real data" standard
   as the earlier findings: queried the live SQLite chunk table directly and found
   **`Tests-*` chunks (92,698) actually outnumber `Base Application` chunks (86,062)**
   — over 40% of the entire AL corpus is test code. Confirmed the mechanism, not just
   the symptom, with a controlled `paths` filter re-run: restricting the same query to
   `*Base Application*` immediately surfaced `GenJnlPostLine.PostCust` at rank 2, score
   0.915 — competitive with everything around it. **The embedding model discriminates
   relevance correctly; the corpus is just structurally flooded with test code that
   textually out-competes real implementations**, because BC's own test codeunits are
   deliberately written with human-readable names and comments
   (`ApplyCustomerLedgerEntry`, `// Verify: Verify Applied Entry from Customer Ledger
   Entry`) that describe the same concepts in natural language the query already uses
   — while the actual implementation is dense AL syntax with internal naming
   (`InitCustLedgEntry`) that doesn't share that vocabulary. Same category of problem
   as finding #3 (corpus composition skewing a technically-correct model), different
   axis (test-vs-implementation instead of docs-vs-code).

   Fixed generically, not with a curated exclude-list: classified every file path in
   the live corpus by directory-segment regex (`Tests?` as a whole word, e.g.
   `Tests-ERM`, `System Application Test`, `Test Library`, `Test Runner`) and checked
   the result against all 17,279 real file paths before trusting it — 2,717 files
   (~15.7%) matched, every single matched top-level directory name was genuinely
   test-related (confirmed no false positives by listing all 90 distinct matched
   directory names), and zero non-matched directories still contained "test" in their
   name (confirmed no false negatives). This is a structural fact about Microsoft's
   own directory layout, not a curated "business concept → object" mapping — it
   generalizes to any query on this corpus, unlike a per-concept exclude-list would.

   Implemented in `chunker/mcp_http_server.py` (our own wrapper code, not a patch to
   `cocoindex-code`'s internals — same precedent as not hand-patching `graphify-al`):
   `search` now overfetches (up to 100, widening in rounds since test chunks can
   dominate the raw top-K) and filters out test-path results before applying the
   caller's `limit`, on by default, with a new `include_tests: bool = False` parameter
   to opt back in when a caller genuinely wants test code. Verified end-to-end: the
   same failing query, with zero manual filtering from the caller, now returns
   `GenJnlPostLine.PostCust` at rank 7 alongside docs and other real `Base
   Application`/`Integration` code, with **zero test codeunits in the top 10** —
   confirmed `include_tests=true` still restores the old (test-heavy) results, so the
   escape hatch works both directions. Deployed to both search servers (8801, 8811).

7. **`query_graph`'s BFS/DFS could not reach a target node even one hop away, and it
   wasn't a ranking problem — it was directional blindness.** Follow-up hands-on
   testing flagged that traversing from the `Customer` table never surfaced
   `Gen. Jnl.-Post Line`, despite finding #2 specifically adding the `references` edge
   to bridge exactly this gap. Diagnosed in two stages, not assumed:

   - First pass looked like a ranking bug: `_subgraph_to_text` (the function that
     renders a BFS/DFS result as text) sorted the output purely by raw node degree,
     ignoring both hop-distance from the seed and the term-relevance score the tool
     *already computes* elsewhere (`_score_nodes`, used only to pick the BFS seed,
     then discarded). Fixed by threading hop-distance (reconstructed from BFS/DFS's
     own edge-discovery order via a new `_hop_distances` helper — no signature change
     to `_bfs`/`_dfs` needed) and the existing relevance score into the sort key:
     `(distance, -relevance, -degree)` instead of `-degree` alone. Backward-compatible
     by construction — omitting the new `distances`/`scores` args degrades to the
     exact old degree-only order, verified with a dedicated regression test.
   - That fix alone didn't solve the actual bug. Checked directly against `graph.json`
     and confirmed the real edge is `GenJnlPostLine --references--> Customer` — i.e.
     it points **from the consumer to the concept**, the opposite direction of the
     traversal. `_load_graph` explicitly loads the graph as a directed `DiGraph`
     (`{"directed": True}`), and `_bfs`/`_dfs` walked it with plain `G.neighbors()`,
     which on a `DiGraph` only returns **successors** (outgoing edges). Since most
     `references`/`table_relation`/`calls` edges point from consumer to concept (a
     codeunit that declares `var Cust: Record Customer` creates an edge *from itself*
     to `Customer`, never the reverse), a BFS/DFS starting *at* a concept table could
     structurally never discover what depends on it — no amount of re-ranking could
     have fixed this, the node was never in the visited set to begin with.
     `shortest_path` already worked around this with `.to_undirected()`; `_bfs`/`_dfs`
     didn't. This bug was invisible to all 2,245 existing tests because the shared
     test fixture (`_make_graph()`) builds a plain undirected `nx.Graph`, which never
     exercises directed-graph behavior at all — confirmed by writing a new
     `_make_digraph()` fixture that mirrors the real corpus's edge direction and
     watching it fail before the fix.

   Fixed with a new `_all_neighbors(G, n)` helper that unions successors and
   predecessors on a directed graph (falls through to plain `neighbors()` on an
   undirected one, so the existing undirected-fixture tests are unaffected), used in
   both `_bfs` and `_dfs`. This also required fixing `_subgraph_to_text`'s edge
   rendering, which did `G[u][v]` assuming discovery order matched real edge
   direction — now safe on either direction and renders using the graph's *real*
   source/target so a `references` edge can't get printed backwards. Added 9 new
   tests (directed-graph BFS/DFS reachability, `_hop_distances`, ranking-with-distance,
   and a byte-identical-output guard for the no-args backward-compat path) — full
   suite at 2,254 passed, 0 failed.

   Verified against real data: `Gen. Jnl.-Post Line` went from **absent from the
   entire 554-node depth-3 traversal** (structurally unreachable, confirmed by
   `shortest_path` independently proving it was 1 hop away via `.to_undirected()`) to
   **reachable at rank 174 of 1,312 direct neighbors** post-fix — the traversal now
   finds it every time regardless of depth (1 or 3), which makes sense since it's a
   direct neighbor.

   Rank 174 still didn't clear the default token budget, and follow-up testing traced
   this to a *third*, distinct bug, not a re-appearance of the first two: the
   relevance scorer's `_SOURCE_MATCH_BONUS` rewards a node whenever the query term
   appears anywhere in its *source file path* — which for a query containing the
   seed's own name (e.g. "customer posting **logic**" from seed `Customer`) means
   literally every method defined inside `Customer.Table.al` scores positively on
   "customer" regardless of what the method actually does, since it trivially lives
   in a file with that name. Confirmed directly against the live graph before
   guessing at a fix: `.CreateAndShowNewInvoice()` defined in `Customer.Table.al`
   scored 1.92 purely from the source-path match, while the byte-identical procedure
   defined in `Vendor.Table.al` scored 0.0 — proving the score came entirely from
   which file the method happened to live in, not any real connection to the query.
   Tested the obvious fix (zero out `_SOURCE_MATCH_BONUS`) empirically before
   adopting it — it made the target's rank slightly *worse* (174→184), because
   `Gen. Jnl.-Post Line`'s own small positive score also came from a legitimate
   source-path match (it lives in a folder literally named `Posting`), so blanket
   removal threw away good signal along with the bad.

   The real fix: a query term that's already present in **every currently-picked
   seed's own label** carries zero discriminating power among that seed's neighbors
   — they're all tautologically "about" the seed by definition of being connected to
   it — but still inflates same-file noise over a genuinely relevant cross-object
   answer that doesn't happen to share the seed's own name. Added `_rank_scores()`,
   which re-scores nodes for the ranking step only (seed *selection* still uses the
   full term set — that part is fine) using just the terms not already satisfied by
   every seed's label, falling back to the original score when every term is
   seed-satisfied (no better signal available). Verified with a hand-built
   before/after simulation against the live graph — `.CreateAndShowNewInvoice()` and
   its unrelated siblings dropped to score 0 once "customer" was excluded, while
   `Gen. Jnl.-Post Line` (whose signal came from "posting", never seed-satisfied)
   was untouched — moving it from **rank 174 to rank 32 of 1,508** (confirmed
   reproducible across 3 fresh process runs, ruling out hash-randomization as a
   confound). Added 3 more regression tests (`_rank_scores` dropping a
   seed-satisfied term, the all-terms-seed-satisfied fallback, and an end-to-end
   ranking-order check) — full suite at 2,257 passed, 0 failed.

   A later independent hands-on check found the same node at rank ~94 instead of 32
   for the identical query — not reconciled with certainty (most likely a
   restart-propagation timing difference between that check and this one, not a
   fresh bug — the underlying `_rank_scores` logic itself was confirmed deterministic
   above), noted honestly rather than asserting a cause that wasn't actually
   verified. Also surfaced a separate, smaller, genuinely open gap: the scorer does
   literal substring matching with no stemming, so a node whose label contains
   "Post" doesn't get credit for query term "posting" — `Gen. Jnl.-Post Line` itself
   scores 0 under the corrected ranking terms and wins its position purely on the
   `-degree` tiebreak, not a text match. Both observations pointed at the same
   pragmatic fix regardless of the exact rank number: raised `query_graph`'s default
   `token_budget` from 2000 to 6000 (all three call sites: `_subgraph_to_text`,
   `_query_graph_text`, and the MCP tool schema/handler default) rather than
   implementing stemming now — stemming would touch `_score_nodes`, which also
   drives seed-picking, `get_node`, and `shortest_path`, a much larger blast radius
   than this specific gap justifies. Verified live post-bump: the exact original
   query, zero manual parameters, now renders 160 nodes (up from ~50) and
   `Gen. Jnl.-Post Line` appears at position 35 of that output — comfortably covering
   both the rank-32 and rank-94 observations either way.

   This is a generic fix (graph traversal direction and rank-by-relevance are
   structural properties of any graph, not AL- or BC-specific), so — like the
   `var_body` fix — a good candidate for a second upstream PR to `graphify-al`, not
   just a local patch. Stemming remains a known, understood, unfixed gap for future
   work. Deployed to both graph servers (8802, 8812).

## Go/no-go detail

**Go** — both layers are real, both scenarios produced grounded, accurate, source-cited
answers from a properly separate client talking only over HTTP MCP. Before this goes
beyond a local PoC:

- **Search-side modality gap (finding #3) — substantially closed** by the Granite
  embedding model swap (finding #5): reversed on the isolated-pair test, reduced by
  ~85% on the live full-corpus ranking. Remaining residual gap is doc near-duplicate
  density in the `dev-itpro` corpus outvoting single strong code matches — a
  dedup/MMR re-ranking pass at query time is the natural next step if this matters
  more once real doc content (see next bullet) is indexed.
- **Test-vs-implementation ranking (finding #6) — fixed.** Test code no longer crowds
  out real `Base Application` implementations by default; `include_tests=true` opt-in
  preserved for when a caller genuinely wants test coverage examples.
- **Graph traversal directional blindness + relevance scoring (finding #7) — fixed.**
  `query_graph` could structurally never discover a node that only referenced the
  seed (the common case for hub tables like `Customer`), independent of ranking; and
  once that was fixed, a second bug (same-file noise outscoring the real answer
  because the seed's own name matches every neighbor's source path) still buried the
  target past the default token budget. Both fixed and verified end-to-end: the
  original failing query now returns the real answer with zero manual parameters.
  Default `token_budget` also raised 2000→6000 as a pragmatic width increase.
  Residual, deliberately unfixed: the relevance scorer does literal substring
  matching with no stemming (a label containing "Post" gets no credit for query
  term "posting") — a real, understood, smaller gap for future work, not chased now
  since a proper fix would touch `_score_nodes`, which also drives seed-picking,
  `get_node`, and `shortest_path`.
  Good candidate for a second upstream PR to `graphify-al`, alongside the `var_body`
  fix (finding #1).
- **Index the actual `dev-itpro` developer docs**, not just the functional docs —
  both test scenarios exposed that this is currently missing and both agents noticed.
- **Validate/enable SQLite WAL mode** and re-test concurrent reindex + live-query
  behavior deliberately (this pass only observed lock contention accidentally).
- **No auth, no rate limiting, no uptime story** — explicitly out of scope here, but
  all needed before any public exposure.
- **Reindex-on-webhook wiring** from the sandbox-history repo's daily automation is
  unbuilt (explicitly out of scope for this PoC) — needs: a webhook receiver, a
  incremental `ccc index` trigger (cheap, since indexing is already incremental), and
  the same treatment for `graphify-al` (which currently has no incremental mode used
  here — reran `python -m graphify update` from scratch each time).
- **Chunk metadata columns** (object type/name, procedure name) are currently baked
  into embedded text rather than being real filterable columns — fine for a PoC,
  worth revisiting for a production index.

## Real-world validation (post-fix)

With all seven findings above deployed, ran the scenario the whole PoC exists to
serve — not another synthetic test query, but the actual question a developer would
ask before extending the base app: *"If I add a field to General Journal Lines and
G/L Entries, how do I make sure it propagates correctly during posting?"*

**One `search` call, zero manual filtering, answered it directly and correctly**:

- `G/L Entry.CopyFromGenJnlLine` (`GLEntry.Table.al:924-967`) — the actual
  field-by-field copy from a posted journal line into the new ledger entry, ending
  in `OnAfterCopyGLEntryFromGenJnlLine(Rec, GenJnlLine)` — the sanctioned extension
  point to subscribe to, rather than modifying base app code.
- A second, more specific mapping point for classification/setup-style fields:
  `CopyPostingGroupsFromGenJnlLine`, with its own
  `OnAfterCopyPostingGroupsFromGenJnlLine` event.
- A genuinely non-obvious detail surfaced unprompted: `Posted Gen. Journal
  Line.InsertFromGenJournalLine` uses `TransferFields(GenJournalLine)`, so a field
  added with the same name/type to both tables propagates automatically with *no*
  event subscription needed there — only the `G/L Entry` leg needs one, since it's a
  differently-shaped table requiring explicit field assignment.

`shortest_path` on the graph server corroborated the direct relationship (`Gen.
Journal Line <--references-- G/L Entry`, 1 hop), consistent with `CopyFromGenJnlLine`
living on `G/L Entry` and referencing the journal line type.

**Token economics, measured on this exact query**: the `search` call plus the
`shortest_path` confirmation totaled **~1,500 tokens** of tool output. The
alternative — reading the two files where this logic actually lives
(`GLEntry.Table.al`: 1,205 lines / 48,814 chars; `GenJnlPostLine.Codeunit.al`:
10,990 lines / 617,370 chars) — would cost roughly **166,000 tokens**, before
accounting for the time spent locating the right procedure in an 11,000-line
codeunit by hand. **~110x cheaper**, and it found the exact right spot on the first
pass.

**Two honest caveats, not smoothed over**:
1. This session's overall token cost is not representative of steady-state usage —
   a large fraction went into debugging the atlas itself (reading `serve.py`
   internals, dumping raw `query_graph` output to disk for inspection, one dump
   exceeding 90K characters). That was infra work to find and fix the findings
   above, not normal query usage, and shouldn't be read as "this is what it costs
   to use."
2. `query_graph` has a real cost tail if misused: `depth=3` combined with a large
   `token_budget` on a hub-heavy seed (e.g. `Customer`, which has 1,500+ direct
   neighbors) can balloon output well past what's useful, even post-fix. `search`
   has no equivalent failure mode — it's consistently cheap regardless of query.
   **Guidance for callers**: reach for `search` first; prefer `shortest_path` /
   `get_node` (both cheap and targeted) over a wide `query_graph` sweep once you
   already know both endpoints you care about.

**Bottom line**: for the exact class of question this PoC was built to answer —
"how does data flow from table A to table B, and where's the extension point" — the
atlas delivers the efficiency it was designed for, end to end, through the actual
served HTTP MCP path, not a shortcut.

## Artifacts

- [chunker/al_chunker.py](chunker/al_chunker.py) — custom AL chunker (tree-sitter-al, object/procedure-aware, context-window-safe)
- [chunker/mcp_http_server.py](chunker/mcp_http_server.py) — HTTP transport wrapper for cocoindex-code's MCP server
- `data/w1-28-src/graphify-out/graph.json` — full structural graph (not committed here, 318 MB)
- `data/.cocoindex_code/target_sqlite.db` — full semantic vector index (not committed here, 1.1 GB)
- `client-session/` — separate Claude Code project used for both test scenario runs, with real transcripts in `scenario1_output.json` / `scenario2_output.json`
