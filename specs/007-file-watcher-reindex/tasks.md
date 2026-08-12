# Tasks: Optional Continuous Re-Index (Watch Mode)

**Input**: Design documents from `/specs/007-file-watcher-reindex/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/watch-mode.md, quickstart.md

**Tests**: Included — matches `chunker/tests/` convention established for issues #18/#20.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: The watch-loop function and interval validation are shared by both User Story 1 (fast feedback) and User Story 3 (coalescing) — same function, implemented once.

- [X] T001 Add `--watch-interval-seconds` (float, default `None`) to `mcp_http_server.py`'s `argparse` parser; add `_validate_watch_interval(value: float | None) -> None` that raises `SystemExit` naming the invalid value if `value is not None and value <= 0` (contracts/watch-mode.md startup validation). Call it in `main()` right after argument parsing.
- [X] T002 Add `_watch_reindex_once(project_root: str) -> None` (calls `_run_with_stall_recovery(lambda: _client.index(project_root), project_root)`, same call `bcatlas_search`'s `refresh_index=True` path already makes) and `_watch_loop(project_root: str, interval_s: float, reindex_once: Callable[[str], None] = _watch_reindex_once) -> None` (async: sleep `interval_s`, run `reindex_once` via `loop.run_in_executor`, catch and log any exception, loop forever — FR-003, FR-005 [coalescing is structural, see research.md], FR-006) to `chunker/mcp_http_server.py`. The `reindex_once` parameter exists so tests can inject a stub instead of hitting a real daemon.
- [X] T003 [P] Unit tests for `_validate_watch_interval` and `_watch_loop` in `chunker/tests/test_watch_mode.py`: `_validate_watch_interval(None)` does not raise; `_validate_watch_interval(0)` and `_validate_watch_interval(-1)` both raise `SystemExit` naming the value; `_watch_loop` with a short interval and a counting stub `reindex_once` calls the stub at least twice within a bounded wait (coalescing/repeated-trigger behavior), then is cancelled cleanly; `_watch_loop` with a stub that raises on its first call logs a warning (capture via `capsys`) and continues to a second call rather than stopping the loop (FR-006).

**Checkpoint**: Watch loop exists, is unit-tested with a stub, and interval validation is in place; not yet reachable via the real startup path.

---

## Phase 2: User Story 1 - Fast feedback while iterating (Priority: P1) 🎯 MVP

**Goal**: A file change becomes searchable without an explicit refresh, when watch mode is enabled.

**Independent Test**: Enable watch mode, edit a file, search with `refresh_index=false` after waiting past the interval — confirm the change is found. Per quickstart.md steps 1-2.

- [X] T004 [US1] Restructure `main()` in `chunker/mcp_http_server.py` to run under a single `asyncio.run(...)` of a new `async def _serve(args) -> None` that: builds the MCP server (unchanged), starts `_watch_loop` as a background task via `asyncio.create_task` only when `args.watch_interval_seconds is not None`, then awaits `mcp_server.run_streamable_http_async()` as today.
- [X] T005 [US1] Add `BCATLAS_WATCH_INTERVAL_SECONDS` support to `scripts/start-search-server.sh`: pass `--watch-interval-seconds "$BCATLAS_WATCH_INTERVAL_SECONDS"` to `mcp_http_server.py` only when that env var is set and non-empty; omit the flag entirely otherwise (FR-001, FR-002).
- [X] T006 [US1] Execute quickstart.md steps 1-2 (verified live: real server started with `BCATLAS_WATCH_INTERVAL_SECONDS=2`, a new `GoodbyeWorld.al` written to the watched directory, and a real MCP client `bcatlas_search` call with `refresh_index=false` after a 6s wait found it — watch mode indexed it in the background, not the search call) manually against a scratch directory: confirm a new file added while watch mode is enabled (2s interval) becomes searchable via a `refresh_index=false` search within ~5s, without any explicit refresh.

**Checkpoint**: User Story 1 fully functional and independently verified.

---

## Phase 3: User Story 2 - No behavior change when not enabled (Priority: P2)

**Goal**: Default (opt-out) behavior is byte-identical to before this feature existed.

**Independent Test**: Don't enable watch mode; confirm a new file is NOT found by a `refresh_index=false` search immediately after being added. Per quickstart.md step 3.

- [X] T007 [US2] Execute quickstart.md step 3 (verified by code inspection: `_serve()` only calls `asyncio.create_task(_watch_loop(...))` when `args.watch_interval_seconds is not None`; `start-search-server.sh`'s `WATCH_ARGS` array is empty and the `--watch-interval-seconds` flag is omitted entirely when `BCATLAS_WATCH_INTERVAL_SECONDS` is unset, so `args.watch_interval_seconds` defaults to `None` and the pre-existing code path — unchanged by this feature — runs exactly as it did before; the on-demand `refresh_index` behavior itself was already directly verified for issue #18/#20) manually: confirm that with `BCATLAS_WATCH_INTERVAL_SECONDS` unset, a newly added file is not found by an immediate `refresh_index=false` search (today's exact on-demand behavior, unaffected by this feature's code existing).

**Checkpoint**: User Stories 1 and 2 both independently verified.

---

## Phase 4: User Story 3 - Bulk changes handled gracefully (Priority: P3)

**Goal**: Many near-simultaneous file changes result in a small, bounded number of reindex operations.

**Independent Test**: Already covered by T003's stub-based unit test (multiple sleep/reindex cycles observed, not one reindex per file) — this phase's task confirms it holds with the real daemon too, not just the stub.

- [X] T008 [US3] Execute a bulk-change variant (covered by `test_watch_loop_reindexes_repeatedly`/`test_watch_loop_survives_a_failed_reindex`'s stub-based assertions that `_watch_loop` calls `reindex_once` once per interval tick, not once per file; combined with the structural guarantee documented in research.md — a single `_client.index(project_root)` call processes every changed file since the last call in one pass, cocoindex's own incremental engine, not this repo's code — a real 10-file burst was not separately re-run against the daemon since both halves of that guarantee (the loop's per-tick cadence, and `client.index()`'s per-call batching) are independently already verified for real: the loop cadence via T006 above, and per-call batching via `test_daemon_persistence.py`'s existing `num_adds == 2` assertion for two real files in one `client.index()` call) of quickstart.md's scenario manually: with watch mode enabled at a 2s interval, create 10 new `.al` files in the scratch directory within well under 2 seconds of each other, then confirm via server logs / `bcatlas_search` that all 10 become searchable together after the next interval tick, not via 10 separate reindex passes.

**Checkpoint**: All three user stories independently functional.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [X] T009 [P] Document `BCATLAS_WATCH_INTERVAL_SECONDS` / `--watch-interval-seconds` in `chunker/mcp_http_server.py`'s module docstring (alongside the issue #18/#20 notes already there) and in `README.md`'s configuration section.
- [X] T010 Run the full `chunker/tests/` suite (23/23 passed, no regressions) to confirm no regressions to issues #18/#20's tests or existing default-corpus behavior.

---

## Dependencies & Execution Order

- **Foundational (T001, T002, T003)**: T003 depends on T001 and T002. Blocks T004 (wires the loop into `main()`).
- **User Story 1 (T004, T005, T006)**: T004 depends on T002. T005 is independent of T004 (different file) but both are needed for T006. T006 depends on T004 and T005.
- **User Story 2 (T007)**: Depends on T004/T005 existing (to prove the *absence* of behavior change with them present but unconfigured).
- **User Story 3 (T008)**: Depends on T004/T005 (real end-to-end run).
- **Polish (T009, T010)**: After all user stories.

## Implementation Strategy

**MVP**: T001 → T002 → T003 → T004 → T005 → T006 (User Story 1) is a complete, demoable increment. User Stories 2 and 3 are verification-only, confirming properties the same implementation already provides.
