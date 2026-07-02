# Phase 1 Data Model: Multi-Country, Multi-Version Serving

No new persistent database is introduced (research.md). These entities describe the shapes
passed between tools and held in the on-disk warm-directory layout and in-memory build queue
state — not database tables.

## Country

| Field | Type | Notes |
|---|---|---|
| `code` | string | Short code, e.g. `w1`, `us`, `de` — the branch-name prefix before `-<major>` |
| `display_name` | string | Human-usable label for discovery output (FR-001) — derived, not stored upstream |

Source of truth: distinct branch-name prefixes on the upstream repository (git ls-remote).

## Version

| Field | Type | Notes |
|---|---|---|
| `country` | string | FK to Country.code |
| `commit_sha` | string | Exact, immutable identifier |
| `version_string` | string | e.g. `w1-28.2.50931.52151` — the commit message itself |
| `major_minor` | string | e.g. `28.2` — parsed prefix of `version_string`, used for "latest within X" resolution |
| `build_number` | int | Parsed trailing numeric component, used to pick "latest" among matches |

Immutable once resolved (constitution Principle III) — a `commit_sha` never needs
re-resolution once obtained.

## VersionSpec (input, not stored)

| Field | Type | Notes |
|---|---|---|
| `country` | string | required |
| `raw` | string | either an exact `version_string`/`commit_sha`, or a loose spec like `"28.1"` (major.minor only) |

Resolution rule (FR-003, FR-004, FR-005): if `raw` matches an existing exact
`version_string`/`commit_sha`, resolve to it directly. Otherwise, if `raw` matches exactly
one `major_minor` prefix pattern, resolve to the single highest-`build_number` `Version`
within that prefix. If zero or more-than-one interpretation remains ambiguous, resolution
MUST fail explicitly (FR-005) — never guess.

## Symbol

| Field | Type | Notes |
|---|---|---|
| `object_type` | string | e.g. `codeunit`, `table`, `page` |
| `object_name` | string | |
| `procedure_name` | string \| null | null when the symbol is the object itself |

Resolved independently within each queried `Version`'s blob — a `Symbol` is a name, not a
location; its line/byte span is only meaningful relative to one specific `Version`.

## SymbolSpan (derived, per-Version)

| Field | Type | Notes |
|---|---|---|
| `version` | Version | which version this span was extracted from |
| `text` | string | full extracted source text for the symbol (object header or full procedure/trigger body, matching existing `get_signature`/`get_procedure_body`/`get_object_source` semantics) |
| `found` | bool | false when the symbol doesn't exist in this version (edge case: added/removed between versions — MUST be reported as such, not an error, per spec Edge Cases) |

## DiffResult

| Field | Type | Notes |
|---|---|---|
| `scope` | `"file"` \| `"symbol"` | required — never absent (FR-007) |
| `country` | string | |
| `from_version` / `to_version` | Version | |
| `path` | string \| null | required when `scope == "file"` |
| `symbol` | Symbol \| null | required when `scope == "symbol"` |
| `diff_text` | string | unified-diff-style text between the two resolved spans/file contents |
| `from_found` / `to_found` | bool | for symbol scope — surfaces the added/removed edge case explicitly |

## SymbolHistoryStep

| Field | Type | Notes |
|---|---|---|
| `version` | Version | the commit at which this step's text was captured |
| `text` | string | the symbol's resolved text at this version |
| `changed_from_previous` | bool | always true for included steps — steps where the text didn't change are filtered out before being returned (FR-008) |

## SymbolHistoryResult

| Field | Type | Notes |
|---|---|---|
| `symbol` | Symbol | |
| `country` | string | |
| `from_version` / `to_version` | Version | range boundaries as requested |
| `granularity` | `"endpoints"` \| `"full"` | caller-selected (FR-009) |
| `steps` | list[SymbolHistoryStep] | only real-change points; length 2 when `granularity == "endpoints"` (start+end, even if unchanged in between — still meaningful to confirm "no change") |

## Build

| Field | Type | Notes |
|---|---|---|
| `country` | string | |
| `version` | Version | |
| `state` | `"queued"` \| `"in_progress"` \| `"ready"` \| `"failed"` | |
| `requested_at` | timestamp | for queue ordering/observability |
| `staging_path` | string | write target during build — never opened by a serving process |
| `served_path` | string \| null | set only after successful atomic promotion; this is the only path a serving process ever opens |
| `base_sibling` | (country, version) \| null | which already-warm pair, if any, this build was cloned-and-patched from (null = cold build) |

State transitions: `queued → in_progress → ready`, or `→ failed` from either of the first
two. A `failed` build does not produce or leave behind a `served_path` — a retry is a new
`Build` from `queued`, never a resume of a failed one (keeps promotion atomicity simple: a
`served_path` existing is always a true, complete, ready artifact).

## WarmResidencyEntry

| Field | Type | Notes |
|---|---|---|
| `country` | string | |
| `version` | Version | |
| `served_path` | string | |
| `last_accessed_at` | timestamp | updated on every read served from this entry |
| `size_bytes` | int | for disk-budget accounting |

Eviction (FR-015, FR-016): a sweep removes the `served_path` directory for entries beyond the
configured disk budget, oldest `last_accessed_at` first, only ever among entries with no
in-flight `Build` referencing them as a `base_sibling`. Eviction never removes the ability to
rebuild — the underlying `Version.commit_sha` remains resolvable and re-fetchable from
upstream at any time (constitution Principle III).
