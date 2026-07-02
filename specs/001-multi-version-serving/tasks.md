---
description: "Task list for Multi-Country, Multi-Version Serving"
---

# Tasks: Multi-Country, Multi-Version Serving

**Input**: Design documents from `specs/001-multi-version-serving/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md
(all present)

**Tests**: included only for the pure-logic pieces called out in plan.md's Testing section
(version resolution, symbol-span extraction/comparison, build queue coalescing, eviction
policy) — live end-to-end verification via quickstart.md is the primary acceptance
mechanism (T035), not replaced by these.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 (discover/resolve), US2 (diff/history), US3 (build/serve)

---

## Phase 1: Setup

- [ ] T001 Create `registry/` uv project skeleton: `registry/pyproject.toml`,
  `registry/registry/__init__.py`, `registry/tests/__init__.py`
- [ ] T002 Create `build/` uv project skeleton: `build/pyproject.toml`,
  `build/build/__init__.py`, `build/tests/__init__.py`
- [ ] T003 [P] Configure `registry/pyproject.toml`: path dependency on `tools/graphify-al`
  (reuse its `_AL_CONFIG`/tree-sitter-al setup), `mcp` SDK dependency matching
  `chunker`/`aggregator`'s existing versions
- [ ] T004 [P] Configure `build/pyproject.toml`: `mcp` SDK dependency, no ML/embedding
  dependency directly (invokes `cocoindex-code`'s `ccc` CLI as a subprocess, doesn't import
  its embedding stack in-process)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: primitives every user story depends on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T005 Implement `registry/registry/git_ops.py`: `list_branches()` (ls-remote),
  `fetch_commit(sha)` (scoped `git fetch origin <sha> --depth 1` into one shared local
  mirror, not per-request clones), `read_blob(sha, path)` (`git show <sha>:<path>`),
  `log_for_path(path, from_sha, to_sha)` (commits touching a file within a range)
- [ ] T006 [P] Implement `registry/registry/symbols.py`: `find_symbol_span(source_bytes,
  object_type, object_name, procedure_name)` — locates a named object/procedure in an
  arbitrary blob via `tree-sitter-al` (extends `tools/graphify-al/graphify/source_lookup.py`'s
  pattern to lookup-by-name instead of lookup-by-line, since diff/history targets have no
  graph node), returns the same header/full-text extraction shape as
  `source_lookup.get_signature`/`get_procedure_body`/`get_object_source`
- [ ] T007 [P] Implement `build/build/layout.py`: warm-directory convention
  (`data/warm/<country>/<version>/{search,graph}`) and staging-directory convention
  (`data/staging/<build-id>/...`), pure path-computation functions, no I/O side effects
- [ ] T008 Write `registry/tests/test_git_ops.py`: unit tests for `fetch_commit`/`read_blob`
  against the real upstream repository (network-dependent — mark/skip appropriately in CI,
  matches how this session validated these operations directly against GitHub)

**Checkpoint**: foundational primitives ready — user story phases can now proceed

---

## Phase 3: User Story 1 - Discover and resolve a version (Priority: P1) 🎯 MVP

**Goal**: a calling agent can list countries, list a country's versions, and resolve any
version spec (exact or loose) to a single unambiguous commit — spec Acceptance Scenarios 1-5

**Independent Test**: quickstart.md "US1 — Discover and resolve a version" section, run
against a live registry server through the aggregator, with no other user story implemented

- [ ] T009 [P] [US1] Implement `registry/registry/resolver.py`: parse a `VersionSpec`
  (exact `version_string`/`commit_sha` vs. loose `major_minor` spec per data-model.md),
  resolve exact matches directly, resolve loose specs to the single highest-`build_number`
  match, fail explicitly (never guess) on zero or ambiguous matches — FR-003, FR-004, FR-005
- [ ] T010 [US1] Write `registry/tests/test_resolver.py`: unit tests for exact resolution,
  loose "latest within X" resolution, not-found, and ambiguous-match rejection (depends on
  T009)
- [ ] T011 [US1] Implement `bcatlas_list_countries` tool in `registry/registry/mcp_server.py`
  per `contracts/registry-tools.md` (depends on T005)
- [ ] T012 [US1] Implement `bcatlas_list_versions` tool in
  `registry/registry/mcp_server.py` per `contracts/registry-tools.md` (depends on T005, T009)
- [ ] T013 [US1] Implement `bcatlas_resolve_version` tool in
  `registry/registry/mcp_server.py` per `contracts/registry-tools.md`, including the shared
  error shape for upstream-unavailable (depends on T009)
- [ ] T014 [US1] Add `scripts/start-registry-server.sh`, matching the existing
  `scripts/start-search-server.sh`/`start-graph-server.sh` pattern
- [ ] T015 [US1] Wire `bcatlas_list_countries`/`bcatlas_list_versions`/
  `bcatlas_resolve_version` proxy tools into `aggregator/unified_mcp_server.py`, update
  `_AGGREGATOR_INSTRUCTIONS` to describe the new discovery-first usage order

**Checkpoint**: US1 fully functional and independently testable/demonstrable

---

## Phase 4: User Story 2 - Diff across versions (Priority: P2)

**Goal**: file/symbol-scoped diffing between two resolved versions, plus a multi-step
symbol change-history chain — spec Acceptance Scenarios 1-5

**Independent Test**: quickstart.md "US2 — Diff across versions" section, run against a live
registry server (US1's resolver is a direct dependency, but none of US1's MCP tools need to
be wired for this story's own tools to be tested)

- [ ] T016 [P] [US2] Implement `registry/registry/diff.py`: file-scoped diff via
  `git_ops.py`'s plumbing, symbol-scoped diff via blob fetch + `symbols.py` + text diff of
  the two extracted spans; reject requests with neither `path` nor a symbol triple —
  FR-006, FR-007 (depends on T005, T006)
- [ ] T017 [P] [US2] Implement `registry/registry/history.py`: `git log` scoped to the
  symbol's containing file across the resolved version range, per-touching-commit
  re-extraction via `symbols.py`, filtered to only steps where the extracted text actually
  changed — FR-008, FR-009 (depends on T005, T006)
- [ ] T018 [US2] Write `registry/tests/test_diff.py` and `registry/tests/test_history.py`:
  unit tests using real blobs fetched once and cached as test fixtures (avoid live network
  calls per test run) — cover the added/removed-symbol edge case and the
  touched-but-unchanged-symbol filtering case explicitly (depends on T016, T017)
- [ ] T019 [US2] Implement `bcatlas_diff` tool in `registry/registry/mcp_server.py` per
  `contracts/registry-tools.md` (depends on T016, T009)
- [ ] T020 [US2] Implement `bcatlas_symbol_history` tool in
  `registry/registry/mcp_server.py` per `contracts/registry-tools.md` (depends on T017, T009)
- [ ] T021 [US2] Wire `bcatlas_diff`/`bcatlas_symbol_history` proxy tools into
  `aggregator/unified_mcp_server.py`

**Checkpoint**: US1 and US2 both independently functional

---

## Phase 5: User Story 3 - Query a completely different version end-to-end (Priority: P3)

**Goal**: any resolved (country, version) becomes buildable and servable on demand, via a
build/serve split that never risks concurrent write/read on the same artifact, reuses
already-warm content when possible, and evicts idle residency automatically — spec
Acceptance Scenarios 1-6

**Independent Test**: quickstart.md "US3 — Query a completely different version end-to-end"
and "Eviction check" sections, run against live registry/build/search/graph/aggregator
servers

- [ ] T022 [US3] Implement `build/build/promote.py`: build into a staging path (from
  `layout.py`), promote to the served path via atomic `os.rename` only after a build
  succeeds — constitution Principle II
- [ ] T023 [US3] Implement `build/build/queue.py`: bounded-concurrency worker pool
  (config-driven max concurrent GPU-bound builds), in-flight request coalescing keyed by
  (country, version) — FR-017
- [ ] T024 [US3] Implement `build/build/incremental.py`: select the nearest already-warm
  sibling (same-country nearest version, else any high-content-overlap pair, else none/cold),
  clone its project directory into a fresh staging path, compute the git diff between the
  sibling's and target's commits, overwrite only the diffed files, invoke `cocoindex-code`'s
  stock `ccc index` against the staging path, then re-run `graphify-al`'s extraction (full
  re-extract — no ML cost, incremental graph extraction not required for this cut) — FR-014
- [ ] T025 [US3] Implement `build/build/eviction.py`: LRU/TTL sweep over
  `WarmResidencyEntry` records under a configured disk budget, oldest `last_accessed_at`
  first, skipping any entry currently referenced as another build's `base_sibling` — FR-015,
  FR-016
- [ ] T026 [US3] Write `build/tests/test_queue.py` and `build/tests/test_eviction.py`: unit
  tests for coalescing behavior and eviction ordering/skip-in-flight-sibling logic (depends
  on T023, T025)
- [ ] T027 [US3] Implement `bcatlas_request_version`/`bcatlas_version_status` tools in
  `build/build/mcp_server.py` per `contracts/build-serve-tools.md` (depends on T022-T025,
  T009 for spec resolution)
- [ ] T028 [US3] Refactor `chunker/mcp_http_server.py` to multi-tenant: accept a resolved
  warm-directory path per `search` call instead of one `project_root` bound at process
  startup; LRU pool of open per-(country,version) SQLite handles; one shared
  embedding-capable process (CPU query-time embedding, per research.md) rather than a model
  load per version — constitution Principle II
- [ ] T029 [US3] Refactor `tools/graphify-al/graphify/serve.py` (existing fork) to
  multi-tenant the same way: accept a resolved warm-directory path per tool call instead of
  one `graph_path` bound at startup
- [ ] T030 [US3] Add `country`/`version` routing parameters (default: `"w1"` / that
  country's currently-warmest version, preserving today's zero-argument behavior) to the
  existing `bcatlas_search`/`bcatlas_query_graph`/`bcatlas_get_node`/etc. proxy tools in
  `aggregator/unified_mcp_server.py`; resolve to a served path via the build service's
  warm-residency state before forwarding, returning the `bcatlas_version_status` "not ready"
  shape rather than querying a wrong/partial artifact — FR-012
- [ ] T031 [US3] Wire `bcatlas_request_version`/`bcatlas_version_status` proxy tools into
  `aggregator/unified_mcp_server.py`
- [ ] T032 [US3] Add `scripts/start-build-server.sh`, matching the existing server-startup
  script pattern

**Checkpoint**: all three user stories independently functional; the existing w1-28 setup
continues working as one warm entry among possibly several (per plan.md's Structure
Decision), not a special case

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T033 [P] Update `README.md` Quick Start and architecture diagram to include the new
  registry/build servers and the country/version query pattern
- [ ] T034 [P] Update `CLAUDE.md`'s "Where things stand today" section to move completed
  pieces from "designed, not built" into "built and running"
- [ ] T035 Run `quickstart.md` end-to-end from a separate MCP client session against live
  servers, covering all three user stories plus the eviction check; record actual observed
  wall-clock numbers for cold vs. incremental builds (SC-006) rather than assuming the
  ~1%/~87% file-overlap figures transfer directly to time saved
- [ ] T036 Verify eviction under a real forced-residency-pressure scenario (request enough
  distinct (country, version) pairs to exceed the configured disk budget) and confirm a
  reclaimed pair rebuilds successfully on re-request — SC-007, SC-008

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — blocks all user stories
- **US1 (Phase 3)**: depends on Foundational only
- **US2 (Phase 4)**: depends on Foundational + `resolver.py` (T009 from US1) for resolving
  `from_spec`/`to_spec` — does NOT depend on US1's MCP tools (T011-T015) being wired
- **US3 (Phase 5)**: depends on Foundational + `resolver.py` (T009) for the same reason;
  independent of US2 entirely
- **Polish (Phase 6)**: depends on all three user stories being complete

### Within Each User Story

- Pure-logic modules before their MCP tool wrappers
- MCP tool wrappers before aggregator wiring
- Tests for a module follow that module, precede tools built on it

### Parallel Opportunities

- T003/T004 (Setup) in parallel
- T006/T007 (Foundational) in parallel with each other and with T005
- T016/T017 (US2) in parallel with each other, both after Foundational
- T009 (US1's resolver) can start as soon as Foundational is done, in parallel with T016/T017
  once it's clear US2 only needs T009's interface, not its MCP wiring
- T033/T034 (Polish) in parallel

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Setup + Foundational
2. Complete US1 (T009-T015)
3. **STOP and VALIDATE**: run quickstart.md's US1 section against a live registry server
4. This alone is real, demonstrable value — no more guessing at version spec strings

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US1 → validate independently → demonstrable (version discovery/resolution live)
3. US2 → validate independently → demonstrable (diffing/history live, still on today's
   single warm w1-28 version if US3 isn't done yet — diff/history read historical commits
   directly via git, they don't need the build/serve split)
4. US3 → validate independently → demonstrable (arbitrary version querying live)
5. Polish → full quickstart.md run, documentation catch-up
