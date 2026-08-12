# Data Model: Configurable MCP Instructions and Path Filtering

No persisted database entity — a startup-time configuration file, read once per server process.

## Server Presentation Settings

Source: `<project_root>/.bcatlas/mcp_presentation.yml` (optional file; absent = all defaults).

| Field | Type | Default when absent/omitted | Notes |
|---|---|---|---|
| `instructions` | string (multi-line) | current hardcoded `_MCP_INSTRUCTIONS` text (FR-002) | Reported verbatim as the MCP server's `instructions` field. |
| `path_prefixes` | list of strings | dynamically detected default candidates (`w1-28-src`, `docs`, `docs-devitpro`) filtered to those that exist under `project_root`, exactly as today (FR-004) | Used by `_expand_paths_for_corpus_prefixes` in place of `_resolve_corpus_path_prefixes`'s output. Empty list is valid and means "no prefix expansion" (Edge Cases). |

## Validation rules

- File present but not valid YAML → fatal startup error (FR-005).
- File present, valid YAML, but not a mapping at the top level → fatal startup error.
- `instructions` present but not a string → fatal startup error.
- `path_prefixes` present but not a list of strings → fatal startup error.
- Either field simply absent from an otherwise-valid file → that field alone uses its default; the other configured field (if any) still applies.

No state transitions — read once at startup, same lifecycle as issue #18's Source Configuration.
