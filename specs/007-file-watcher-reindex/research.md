# Research: Optional Continuous Re-Index (Watch Mode)

## Decision: short-interval polling of the existing `client.index()` primitive, not a new filesystem-event watcher

- **Investigated**: `cocoindex.App` (`tools/cocoindex-code/.venv/.../cocoindex/__init__.py`, confirmed live via `dir(cocoindex.App)`) exposes only `update`, `update_blocking`, `drop`, `drop_blocking` — no continuous/live-watch mode built into the vendored indexing library itself. The `handle.watch()` seen in `cocoindex_code/project.py`'s `_run_index_inner` is the *progress stream* of a single `update()` call (stats snapshots), not a continuous filesystem watcher — a naming false-friend, confirmed by reading that method directly rather than assumed from the name.
- **Decision**: Implement watch mode as a background `asyncio` task inside `chunker/mcp_http_server.py` that, on a fixed interval, calls the exact same `_client.index(project_root)` call `bcatlas_search`'s existing `refresh_index=True` path already makes — wrapped in the same `_run_with_stall_recovery` helper that path already uses, run via `loop.run_in_executor` the same way.
- **Rationale**: Constitution dev workflow ("Prefer composing existing, verified primitives... reuse is the default, a new primitive needs its own justification"). `_run_with_stall_recovery` + `_client.index()` is already the hardened, tested call path for triggering indexing from this exact file (it recovers from the documented daemon-stall failure mode, which a naive new call site would not). cocoindex-code's own incremental engine is already content-hash-based (confirmed via Principle VIII's prior verification in CLAUDE.md), so a periodic call when nothing changed is cheap (fast diff, `num_unchanged` for everything) rather than a wasted full reprocess.
- **Alternatives considered**: A real filesystem-event watcher (e.g. `watchdog`/`watchfiles`, inotify-based) — rejected for this iteration: adds a new dependency, adds real complexity (recursive-watch fd limits on a large corpus, cross-platform differences), and the spec's own Assumptions explicitly accept "a few seconds," not sub-second reaction, so the added complexity buys accuracy the requirement doesn't ask for. If real-time reaction is ever needed, this can be revisited without changing the public contract (still "watch mode enabled/disabled + how promptly").

## Decision: coalescing (FR-005) falls out of the polling design for free

- **Decision**: No separate debounce timer is needed. Every file change that lands within one polling interval is picked up by the *same* next `_client.index()` call — cocoindex's own incremental engine already processes all changed files in one pass per `update()`, not one pass per file.
- **Rationale**: Directly satisfies FR-005 ("multiple changes... coalesced into a small, bounded number of reindex operations") as a structural property of the chosen mechanism, not an extra feature to build.

## Decision: configuration surface — a new CLI flag on `mcp_http_server.py`, plus the wrapper script's existing env-var-to-flag pattern

- **Decision**: Add `--watch-interval-seconds FLOAT` to `mcp_http_server.py`'s `argparse` parser (default: not set = disabled, matching FR-001/FR-002). `scripts/start-search-server.sh` passes it through only when `BCATLAS_WATCH_INTERVAL_SECONDS` is set, mirroring how it already turns `SEARCH_HOST`/`SEARCH_PORT` env vars into CLI flags.
- **Rationale**: Keeps `mcp_http_server.py`'s own interface (argparse) as the single, directly-testable source of truth (a test can call `main()`/construct the watch task with an explicit interval, no env var indirection needed), while preserving the operator-facing env-var convenience already established for issue #18's `BCATLAS_SOURCE_DIR`. Unlike `BCATLAS_SOURCE_DIR` (which maps onto an *existing* positional arg), there's no existing CLI surface for this, so a new flag is the natural fit rather than overloading an unrelated one.
- **Alternatives considered**: Env-var-only (read directly in `mcp_http_server.py`, no CLI flag) — rejected, inconsistent with how `--host`/`--port` are already real flags in this same file; a flag is more discoverable (`--help`) and directly unit-testable.

## Decision: failure visibility (FR-006) — log and keep retrying, don't crash the server

- **Decision**: The watch loop catches exceptions from each reindex attempt, logs a warning (same `print(..., flush=True)` convention as `_validate_project_root`'s warning from issue #18), and continues looping rather than letting an exception propagate and kill the background task silently.
- **Rationale**: A crashed background task in `asyncio` fails silently unless something awaits it or checks `task.exception()` — logging on every failure and continuing is simpler and more robust than building task-supervision machinery, and satisfies FR-006's "visible, not silently stale" requirement directly. The existing on-demand `refresh_index` search path (untouched by this feature) remains available as a fallback regardless.

## Non-goals confirmed by this research

- No change to the multi-tenant registry/build pipeline — same single default-corpus-serving process as issues #18/#20.
- No hosted-instance deploy/restart — `--watch-interval-seconds`/`BCATLAS_WATCH_INTERVAL_SECONDS` unset (their state on the hosted VM, untouched by this work) means zero behavior change there; verified locally only.
- No new third-party dependency.
