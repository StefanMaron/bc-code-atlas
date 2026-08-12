# Contract: Watch Mode (`--watch-interval-seconds` / `BCATLAS_WATCH_INTERVAL_SECONDS`)

## Consumers

- `chunker/mcp_http_server.py` — new `--watch-interval-seconds` CLI flag.
- `scripts/start-search-server.sh` — passes it through only when `BCATLAS_WATCH_INTERVAL_SECONDS` is set.

## Behavior

| `BCATLAS_WATCH_INTERVAL_SECONDS` | Effective flag passed to `mcp_http_server.py` | Watch mode |
|---|---|---|
| unset or empty | (flag omitted) | disabled — unchanged default behavior (FR-002) |
| set to a positive number | `--watch-interval-seconds <value>` | enabled, polling every `<value>` seconds |

Calling `mcp_http_server.py` directly (bypassing the wrapper script, e.g. in tests) with `--watch-interval-seconds` set has the same effect — the flag is the actual contract; the env var is wrapper-script sugar only.

## Startup validation contract

- `--watch-interval-seconds` given a non-positive value (`<= 0`) → process exits non-zero before binding the HTTP port, with a clear error naming the invalid value (same fail-fast precedent as issue #18's `_validate_project_root`).
- `--watch-interval-seconds` omitted (or `BCATLAS_WATCH_INTERVAL_SECONDS` unset) → watch mode is off; server behaves exactly as it did before this feature existed.

## Runtime contract

- Enabled watch mode never blocks server startup or concurrent `bcatlas_search` requests (FR-007) — reindex calls run off the main event loop via `run_in_executor`, same as the existing `refresh_index=True` search path.
- A failed reindex attempt is logged and the loop continues on the next interval (FR-006) — it never crashes the server process or silently stops.
- Multiple file changes within one interval are covered by the single next reindex call (FR-005) — no per-file reindex operations.

## Non-contract (unchanged)

- MCP tool names, request/response schemas: unchanged (FR-008).
- Issue #18's `BCATLAS_SOURCE_DIR` / startup path validation, issue #20's `.bcatlas/mcp_presentation.yml`: unrelated, independent, unaffected.
- The hosted production instance: `BCATLAS_WATCH_INTERVAL_SECONDS` unset there means zero behavior change (FR-004, SC-005).
