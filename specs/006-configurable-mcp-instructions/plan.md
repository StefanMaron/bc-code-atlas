# Implementation Plan: Configurable MCP Instructions and Path Filtering

**Branch**: `006-configurable-mcp-instructions` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-configurable-mcp-instructions/spec.md`

## Summary

GitHub issue #20, following directly from issue #18 (specs/005-local-source-directory). Let an operator override `chunker/mcp_http_server.py`'s MCP-reported `instructions` text and its search path-filter-prefix list via configuration, so a custom (non-BC) corpus gets accurate agent-facing instructions and correct path-filter expansion instead of hardcoded Business Central text and prefixes. Research (research.md) decided this is a small, repo-owned settings file — `<project_root>/.bcatlas/mcp_presentation.yml` — read directly in `mcp_http_server.py`, not an extension of vendored `cocoindex_code`'s own settings schema.

## Technical Context

**Language/Version**: Python 3.11+ (same file as issue #18: `chunker/mcp_http_server.py`)

**Primary Dependencies**: `PyYAML` (already present transitively via `cocoindex_code.settings`, no new dependency), `mcp`/`FastMCP`

**Storage**: N/A — one small YAML file read once at startup, no persisted state

**Testing**: `chunker/tests/` (pytest) for the new settings-loading/validation logic; manual quickstart.md walkthrough for end-to-end MCP-instructions verification

**Target Platform**: Same server process as issue #18 — Linux, local or hosted

**Project Type**: Existing single service (`chunker/`), no new top-level project

**Performance Goals**: N/A — one small file read/parse at startup, negligible cost

**Constraints**: MUST NOT change default behavior when `.bcatlas/mcp_presentation.yml` is absent (FR-002, FR-004); MUST NOT require touching or restarting the hosted production instance to implement or verify

**Scale/Scope**: One new settings-loading function (~30-40 lines) in `mcp_http_server.py`, wiring it into `create_filtered_mcp_server`, one new test file

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Principle I (Serve Like It's Remote)**: Unaffected — verified the same way as issue #18, via a real MCP client against the running HTTP server (quickstart.md), not in-process. **PASS**.
- **Principle II (Build and Serve Are Separate Resource Pools)**: Unaffected — no build-side change, no new writer/reader concurrency. **PASS**.
- **Principle III / IV**: Not applicable — same single default-corpus-serving process as issue #18, not a new (country, version) artifact. **PASS**.
- **Principle V (Measure, Don't Assume)**: research.md directly inspected `cocoindex_code.settings.ProjectSettings`'s real field set before concluding a new file was needed rather than an extension of that schema, and confirmed PyYAML is already an available dependency by reading `tools/cocoindex-code/src/cocoindex_code/settings.py`'s own import, rather than assuming a new dependency was needed. **PASS**.
- **Principle VI (Minimal, Justified Forks)**: Directly the point of the research.md decision — keeps `cocoindex_code` unmodified by putting this config entirely in `chunker/`, which this repo already owns. **PASS**.
- **Principle VII (Lean, Honest Agent-Facing Output)**: This feature exists specifically to satisfy this principle for the case issue #18 explicitly deferred — a custom corpus can now get instructions text that accurately describes what's indexed instead of a hardcoded BC description. **PASS** (this feature closes the note left open in specs/005-local-source-directory/plan.md's Complexity Tracking).
- **Principle VIII (Deploys Must Not Reset the Serving Index)**: No reindex risk — this doesn't touch indexing at all, only MCP server construction (`_MCP_INSTRUCTIONS`, `_corpus_path_prefixes`), and the hosted `data/` project root has no `.bcatlas/` directory so its behavior is unchanged by construction. Verified locally only. **PASS**.

## Project Structure

### Documentation (this feature)

```text
specs/006-configurable-mcp-instructions/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── settings-file.md
└── tasks.md
```

### Source Code (repository root)

```text
chunker/
├── mcp_http_server.py    # add _load_presentation_settings(project_root), wire into create_filtered_mcp_server
└── tests/
    └── test_presentation_settings.py  # NEW — unit tests for the settings loader
```

**Structure Decision**: Same single existing service (`chunker/`) as issue #18 — no new top-level project, no new script changes needed (the settings file lives inside `project_root`, discovered automatically, not via a new env var/flag).

## Complexity Tracking

No unjustified Constitution violations — none needed.
