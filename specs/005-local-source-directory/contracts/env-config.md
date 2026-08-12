# Contract: `BCATLAS_SOURCE_DIR` environment variable

## Consumer

`scripts/start-search-server.sh`

## Behavior

| `BCATLAS_SOURCE_DIR` | Effective `project_root` passed to `mcp_http_server.py` |
|---|---|
| unset or empty | `$ROOT/data` (unchanged default behavior — FR-002) |
| set to an absolute or relative path | that path, resolved as given |

## Startup validation contract (`chunker/mcp_http_server.py`)

Enforced regardless of how `project_root` was supplied (env var via the wrapper script, or a direct positional arg — both go through the same `main()`):

1. If `project_root` does not exist, or exists but is not a directory → process exits non-zero before binding the HTTP port, with a message identifying the exact configured path (FR-004).
2. If `project_root` exists and is a directory but contains zero `*.al` files (recursive) → process logs a warning naming the path and continues starting (FR-005) — this is a warning, not a fatal error, because an operator may intentionally point at a not-yet-populated directory before adding source.

## Non-contract (unchanged)

- MCP tool names, request/response schemas: unchanged (FR-008).
- `--host` / `--port` CLI flags and `SEARCH_HOST` / `SEARCH_PORT` env vars: unchanged, unaffected by this feature.
