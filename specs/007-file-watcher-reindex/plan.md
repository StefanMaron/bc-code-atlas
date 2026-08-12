# Implementation Plan: Optional Continuous Re-Index (Watch Mode)

**Branch**: `007-file-watcher-reindex` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/007-file-watcher-reindex/spec.md`

## Summary

GitHub issue #21. Let an operator opt into continuous reindexing so file changes under the configured source directory become searchable without an explicit index action or a search's own `refresh_index=True`. Research (research.md) found no built-in continuous-watch primitive in vendored `cocoindex` (only `update`/`update_blocking`), so this composes the existing, already-hardened `_run_with_stall_recovery(lambda: _client.index(project_root), project_root)` call path (the same one `bcatlas_search`'s `refresh_index` already uses) into a background `asyncio` task that runs on a fixed interval when explicitly enabled via a new `--watch-interval-seconds` flag (wired through `BCATLAS_WATCH_INTERVAL_SECONDS` in the wrapper script, same pattern as issue #18's `BCATLAS_SOURCE_DIR`). No new dependency; coalescing (FR-005) falls out of the polling design for free.

## Technical Context

**Language/Version**: Python 3.11+ (same file: `chunker/mcp_http_server.py`)

**Primary Dependencies**: None new — reuses `cocoindex_code.client`, `asyncio` (stdlib)

**Storage**: N/A — no new persisted state

**Testing**: `chunker/tests/` (pytest) for interval-validation and the watch-loop's coalescing/failure-handling logic (with a fake/stubbed indexing call, not a real daemon, to keep the unit test fast — the real daemon path is already covered end-to-end by `test_daemon_persistence.py` and this session's earlier manual quickstart verifications); manual quickstart.md walkthrough for real end-to-end verification.

**Target Platform**: Same server process as issues #18/#20.

**Project Type**: Existing single service (`chunker/`), no new top-level project.

**Performance Goals**: Bounded by the configured interval — no tighter latency requirement (spec Assumptions: "a few seconds," not real-time).

**Constraints**: MUST NOT change default behavior when unset (FR-002); MUST NOT touch or restart the hosted production instance to implement or verify (explicit user instruction, same as issues #18/#20); MUST NOT add a new third-party dependency (research.md decision).

**Scale/Scope**: One new CLI flag, one new async background-task function (~20-30 lines), one wrapper-script edit, one new test file.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Principle I (Serve Like It's Remote)**: Verified via a real MCP client against the running HTTP server (quickstart.md step 2 — search without an explicit refresh, after a background reindex), not in-process. **PASS**.
- **Principle II (Build and Serve Are Separate Resource Pools)**: Unaffected — watch mode calls the same `client.index()` path the serving process already uses for `refresh_index`; no new writer, no change to the build/serve split. **PASS**.
- **Principle III / IV**: Not applicable — same single default-corpus-serving process as issues #18/#20, not a new (country, version) artifact; explicitly out of scope for the immutable multi-tenant build pipeline (spec Assumptions). **PASS**.
- **Principle V (Measure, Don't Assume)**: Directly inspected `cocoindex.App`'s real method list (`dir(cocoindex.App)` against the actual installed package) before concluding no continuous-watch primitive exists, rather than assuming one did or didn't; read `_run_index_inner`'s `handle.watch()` directly to confirm it's a progress stream, not a filesystem watcher, despite the name. **PASS**.
- **Principle VI (Minimal, Justified Forks)**: No vendored-code changes; composes existing `cocoindex_code.client`/`_run_with_stall_recovery` machinery already in this repo's own `chunker/mcp_http_server.py`. **PASS**.
- **Principle VII (Lean, Honest Agent-Facing Output)**: No MCP tool schema/description changes (FR-008) — watch mode is a server-startup concern, invisible to the tool contract itself. **PASS**.
- **Principle VIII (Deploys Must Not Reset the Serving Index)**: This is the most directly relevant principle to get right here — watch mode explicitly reuses the *incremental* `client.index()` call (not a full rebuild), the same call already proven live to resume from on-disk state; and it is strictly opt-in per FR-004/SC-005, so the hosted VM's `data/` corpus (no `BCATLAS_WATCH_INTERVAL_SECONDS` set there) is entirely unaffected by construction. Verified locally only, per quickstart.md, matching the constraint applied to issues #18/#20 this session. **PASS**.

## Project Structure

### Documentation (this feature)

```text
specs/007-file-watcher-reindex/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── watch-mode.md
└── tasks.md
```

### Source Code (repository root)

```text
chunker/
├── mcp_http_server.py    # add --watch-interval-seconds flag, _watch_loop(), wire into main()
└── tests/
    └── test_watch_mode.py  # NEW — unit tests for interval validation + coalescing/failure-handling

scripts/
└── start-search-server.sh  # pass --watch-interval-seconds through from BCATLAS_WATCH_INTERVAL_SECONDS when set
```

**Structure Decision**: Same single existing service (`chunker/`) as issues #18/#20 — no new top-level project.

## Complexity Tracking

No unjustified Constitution violations — none needed.
