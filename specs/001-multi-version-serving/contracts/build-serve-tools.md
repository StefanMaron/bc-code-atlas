# Contract: Build/Serve MCP Tools and Routing Changes

## New tools — served by `build/build/mcp_server.py`

### `bcatlas_request_version`

**Input**: `{ "country": "w1", "spec": "29.1" }` (a `VersionSpec` — resolved internally via
the same resolution logic as `bcatlas_resolve_version`; callers are expected to have already
called that tool first per the intended usage order, but this tool resolves independently
rather than requiring a pre-resolved commit, since FR-011 requires acknowledging a request
immediately and resolution is cheap).

**Output**:
- Already warm: `{ "status": "ready", "country": "w1", "commit_sha": "...", "served_since":
  "..." }` — usable immediately with the existing search/graph tools (see routing change
  below).
- Not warm, build started or coalesced into an in-flight one (FR-017): `{ "status":
  "queued" | "in_progress", "country": "w1", "commit_sha": "...", "eta_hint": "..." }` — an
  immediate acknowledgment distinct from final results (FR-011, spec Acceptance Scenario 1).
- Resolution failure: same shared error shape as `bcatlas_resolve_version`.

### `bcatlas_version_status`

**Input**: `{ "country": "w1", "commit_sha": "..." }`.

**Output**: `{ "state": "queued" | "in_progress" | "ready" | "failed" | "unknown" }` —
`"unknown"` for a commit never requested; callers polling too early get a clear state, never
partial data (FR-012, spec Acceptance Scenario 2).

## Routing change — existing search/graph tools

`bcatlas_search`, `bcatlas_query_graph`, `bcatlas_get_node`, `bcatlas_get_neighbors`,
`bcatlas_get_signature`, `bcatlas_get_procedure_body`, `bcatlas_get_object_source`,
`bcatlas_get_community`, `bcatlas_god_nodes`, `bcatlas_graph_stats`, `bcatlas_shortest_path`
each gain two new optional parameters: `country` (default `"w1"`) and `version` (default:
that country's currently-warmest/most-recently-requested version — preserves today's
zero-argument behavior for existing callers who don't know this feature exists yet).

Aggregator forwarding resolves `(country, version)` to a `served_path` via the build
service's warm-residency state (`bcatlas_version_status` internally) before forwarding to the
now-multi-tenant search/graph backends; a request for a `(country, version)` that isn't
`"ready"` returns the same `bcatlas_version_status`-shaped "not ready" response rather than
querying a wrong or partial artifact (FR-012).

## Cross-cutting contract: no direct filesystem exposure

None of the tools above ever return a raw filesystem path to the caller — `served_path` /
`staging_path` (data-model.md `Build`) are internal routing details between the aggregator
and the search/graph/build backends, never part of any MCP tool's response shape. This keeps
the constitution Principle I boundary intact (callers only ever see MCP tool results, never
infra internals) and avoids leaking a path a caller could misuse (e.g. a resolved-but-evicted
path).
