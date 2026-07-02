# Contract: Registry MCP Tools

Served by `registry/registry/mcp_server.py`, forwarded through the aggregator (same pattern
as existing `bcatlas_*` tools — see constitution/README for the naming rationale).

## `bcatlas_list_countries`

**Input**: none.

**Output**: `{ "countries": [{ "code": "w1", "display_name": "..." }, ...] }` — a finite,
deduplicated list derived from upstream branch-name prefixes (FR-001). Never the raw 546
branch names.

## `bcatlas_list_versions`

**Input**: `{ "country": "w1" }` (required).

**Output**: `{ "country": "w1", "major_versions": [{ "major_minor": "28.2", "latest_build":
"w1-28.2.50931.52182" }, ...] }` — summarized by major.minor, not one entry per raw build
(FR-002). Error (not empty list) if `country` doesn't exist.

## `bcatlas_resolve_version`

**Input**: `{ "country": "w1", "spec": "28.1" }` or `{ "country": "w1", "spec":
"w1-28.2.50931.52151" }` (required: both fields).

**Output on success**: `{ "resolved": true, "country": "w1", "commit_sha": "...",
"version_string": "w1-28.2.50931.52151" }`.

**Output on failure**: `{ "resolved": false, "reason": "not_found" | "ambiguous", "detail":
"..." }` — MUST NOT fall back to guessing a version (FR-005, spec Acceptance Scenario 5).

## `bcatlas_diff`

**Input**:
```json
{
  "country": "w1",
  "from_spec": "28.1",
  "to_spec": "28.2",
  "scope": "symbol",
  "path": null,
  "object_type": "codeunit",
  "object_name": "Sales-Post",
  "procedure_name": "PostSalesDoc"
}
```
Exactly one of `path` (scope=`"file"`) or the `object_type`/`object_name`/`procedure_name`
triple (scope=`"symbol"`) MUST be supplied — a request with neither MUST be rejected
(FR-007, spec Acceptance Scenario 3), never producing a whole-repository diff.

**Output**: a `DiffResult` (see data-model.md) — `diff_text` plus `from_found`/`to_found` so
an added/removed symbol between the two versions is reported explicitly, not treated as an
error (spec Edge Cases).

## `bcatlas_symbol_history`

**Input**:
```json
{
  "country": "w1",
  "from_spec": "28.1",
  "to_spec": "28.2",
  "object_type": "codeunit",
  "object_name": "Sales-Post",
  "procedure_name": "PostSalesDoc",
  "granularity": "full"
}
```
`granularity` is `"endpoints"` (default) or `"full"` (FR-009).

**Output**: a `SymbolHistoryResult` (see data-model.md) — `steps` contains only points where
the symbol's own resolved text changed, never every commit that touched the containing file
(FR-008, SC-004).

## Shared error shape

Any tool above returns a structured error (not a silent empty/wrong result) when:
- the upstream repository is unreachable at request time (spec Edge Cases) — `{ "error":
  "upstream_unavailable", "detail": "..." }`;
- a `spec` resolves ambiguously — surfaced via `bcatlas_resolve_version`'s `resolved: false`
  shape, and any tool that resolves specs internally (diff, symbol_history) reuses the same
  shape rather than a bare exception.
