# Implementation Plan: Configurable Local AL Source Directory

**Branch**: `005-local-source-directory` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-local-source-directory/spec.md`

## Summary

GitHub issue #18. Let an operator point the search/chunker service (`chunker/mcp_http_server.py`) at any local directory of AL source instead of the built-in Microsoft BC corpus, via configuration alone. Research (research.md) found the hard part already exists — `mcp_http_server.py`'s `main()` already takes `project_root` as a positional argument; the only real hardcode is in `scripts/start-search-server.sh`, which always passes `$ROOT/data`. The actual scope: (1) an optional `BCATLAS_SOURCE_DIR` env var in that script, (2) startup path validation in `mcp_http_server.py` (fail fast on missing path, warn on empty), (3) a ship-with-the-repo AL-scoped `.cocoindex_code/settings.yml` template so a fresh custom directory gets AL-aware chunking instead of cocoindex-code's generic multi-language defaults.

## Technical Context

**Language/Version**: Python 3.11+ (matches `chunker/pyproject.toml` / `tools/cocoindex-code`)

**Primary Dependencies**: `cocoindex-code` (vendored, unmodified per constitution Principle VI), `bc-al-chunker` (`chunker/al_chunker.py`), `mcp`/`FastMCP`

**Storage**: N/A — no new persisted state; reads existing `.cocoindex_code/` SQLite state at whatever `project_root` is configured, exactly as today

**Testing**: `chunker/tests/` (pytest) for the new startup-validation logic; manual quickstart.md walkthrough against a scratch local AL directory for end-to-end verification (no hosted VM involved)

**Target Platform**: Linux server process (same as today — `scripts/start-search-server.sh`), runnable identically on a developer machine

**Project Type**: Existing single service (`chunker/`) — no new top-level project

**Performance Goals**: N/A — no performance-sensitive path touched; startup validation is a single `Path.exists()`/glob check, negligible cost

**Constraints**: MUST NOT change default behavior when `BCATLAS_SOURCE_DIR` is unset (FR-002); MUST NOT require touching or restarting the hosted production instance to implement or verify (explicit user instruction this session)

**Scale/Scope**: One additional env var, ~15-30 lines of startup validation in `mcp_http_server.py`, one new template YAML file, one script edit

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Principle I (Serve Like It's Remote)**: Unaffected — this feature doesn't change how the server is reached (still MCP over HTTP); quickstart.md verifies it the same way (a separate client session against the running HTTP server), not in-process. **PASS**.
- **Principle II (Build and Serve Are Separate Resource Pools)**: Unaffected — no build-side change; a custom local directory still goes through the same single serving process, no writer/reader concurrency introduced. **PASS**.
- **Principle III / IV (Immutable versions / Unbounded scope, bounded residency)**: Not applicable — a custom local source directory is explicitly a single-corpus mode of the *default* corpus slot, not a new (country, version) pair in the registry-driven multi-tenant system (see spec Assumptions). No change to eviction/residency logic. **PASS**.
- **Principle V (Measure, Don't Assume)**: Applied directly in research.md — rather than assuming `project_root` was hardcoded, the actual `mcp_http_server.py` source was read, confirming it already takes `project_root` as a parameter and that the only real hardcode is one line in the wrapper script; also directly verified `ProjectSettings`'s default include/chunker behavior in `tools/cocoindex-code/src/cocoindex_code/settings.py` rather than assuming a fresh directory would "just work" for AL. **PASS**.
- **Principle VI (Minimal, Justified Forks)**: No changes to vendored `cocoindex-code`; the AL-scoped settings template is a repo-owned config file consumed by the unmodified tool's own settings-loading mechanism, not a code fork. **PASS**.
- **Principle VII (Lean, Honest Agent-Facing Output)**: No MCP tool description changes in this feature (that's issue #20's scope) — `_MCP_INSTRUCTIONS` stays as-is here, which is technically inaccurate when a custom directory is configured (it still says "Microsoft Dynamics 365 Business Central"). Documented as a known limitation resolved by issue #20's feature (specs/006), not duplicated here. **PASS with note** (see Complexity Tracking).
- **Principle VIII (Deploys Must Not Reset the Serving Index)**: Directly protected by construction — `BCATLAS_SOURCE_DIR` unset (its state on the hosted VM, untouched by this work) means the wrapper script's behavior is byte-identical to today, so no reindex risk exists for the hosted instance. Verified locally only, per quickstart.md. **PASS**.

## Project Structure

### Documentation (this feature)

```text
specs/005-local-source-directory/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── env-config.md    # Phase 1 output
└── tasks.md              # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
chunker/
├── mcp_http_server.py         # add startup path-validation (FR-004/FR-005) in main()
├── templates/
│   └── al-source-settings.yml # NEW — AL-scoped cocoindex-code settings template
└── tests/
    └── test_source_dir_validation.py  # NEW — unit tests for the validation logic

scripts/
└── start-search-server.sh     # use BCATLAS_SOURCE_DIR if set, else $ROOT/data (unchanged default)
```

**Structure Decision**: Single existing service (`chunker/`), no new top-level project. All changes are additive to files that already exist plus one new template file and one new test file, matching the project's existing single-project layout (no `src/`/`backend/`/`frontend/` split anywhere in this repo).

## Complexity Tracking

> No unjustified Constitution violations. One documented, deliberately deferred note from the Constitution Check above:

| Note | Why Deferred | Where It's Actually Handled |
|-----------|------------|-------------------------------------|
| `_MCP_INSTRUCTIONS` still says "Business Central" even when a custom non-BC directory is configured (Principle VII: accurate agent-facing descriptions) | Making instructions corpus-aware requires the configurable-instructions mechanism, which is a separate, already-filed feature (GitHub issue #20) with its own spec — building a one-off partial fix here would duplicate that design | `specs/006-configurable-mcp-instructions` (issue #20), planned next in this session |
