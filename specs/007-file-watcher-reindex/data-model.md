# Data Model: Optional Continuous Re-Index (Watch Mode)

No persisted database entity — a startup-time configuration switch plus a long-lived background task, same lifecycle class as issues #18/#20.

## Continuous Reindexing Configuration

| Field | Type | Source | Default | Notes |
|---|---|---|---|---|
| `watch_interval_seconds` | float \| None | `--watch-interval-seconds` CLI flag (mcp_http_server.py), set by `scripts/start-search-server.sh` from `BCATLAS_WATCH_INTERVAL_SECONDS` when present | `None` (disabled — FR-001/FR-002) | When set, MUST be a positive number; a non-positive value is a startup configuration error (fail fast, same precedent as issue #18's path validation). |

## Watch Loop (runtime, not persisted)

An `asyncio` background task, started only when `watch_interval_seconds` is not `None`:

1. Sleep `watch_interval_seconds`.
2. Call the existing `_run_with_stall_recovery(lambda: _client.index(project_root), project_root)` off the event loop (`run_in_executor`), identical to `bcatlas_search`'s `refresh_index=True` path.
3. On success or failure, log nothing extra on success (matches today's silent successful refresh); on failure, log a warning and continue looping (FR-006) — never exits the loop or crashes the server.
4. Repeat indefinitely for the lifetime of the server process.

No state transitions beyond running/not-running, decided once at startup from the configuration above.
