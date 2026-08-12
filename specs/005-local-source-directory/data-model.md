# Data Model: Configurable Local AL Source Directory

This feature has no persisted database entity — it is a startup-time configuration switch. The one conceptual entity from the spec:

## Source Configuration

Resolved once, at `chunker/mcp_http_server.py` process startup.

| Field | Type | Source | Notes |
|---|---|---|---|
| `project_root` | path | `BCATLAS_SOURCE_DIR` env var if set, else `<repo>/data` | Passed to `create_filtered_mcp_server(project_root)` exactly as today — no new parameter threading needed since it's already the function's existing argument. |
| validity | derived | filesystem check at startup | Must exist and be a directory (FR-004); else the process MUST fail to start with a clear error naming the configured path. |
| `.al` file presence | derived | filesystem check at startup | Directory exists but has zero `.al` files under it → log a warning, still start (FR-005). |

No state transitions: this is a read-once-at-startup value, not something that changes while the process runs (switching corpora requires a restart, matching User Story 3's "remove the override and restart" framing).
