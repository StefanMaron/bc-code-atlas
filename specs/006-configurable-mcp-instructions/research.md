# Research: Configurable MCP Instructions and Path Filtering

## Decision: a new, repo-owned settings file — not an extension of cocoindex-code's own `settings.yml`

- **Investigated**: `data/.cocoindex_code/settings.yml` is parsed into `cocoindex_code.settings.ProjectSettings` (`tools/cocoindex-code/src/cocoindex_code/settings.py`), a dataclass with a fixed field set (`include_patterns`, `exclude_patterns`, `language_overrides`, `chunkers`). Adding `instructions`/`path_prefixes` fields there would mean forking the vendored `cocoindex_code` package.
- **Decision**: Introduce a new file, `<project_root>/.bcatlas/mcp_presentation.yml`, read directly by `chunker/mcp_http_server.py` (our own code) — not touched by or coupled to cocoindex-code's settings loader at all.
- **Rationale**: Constitution Principle VI (Minimal, Justified Forks) — `cocoindex_code` is kept as an unmodified vendored checkout; this config is purely presentational (MCP instructions text, search-filter prefix list) and has nothing to do with indexing itself, so it belongs entirely in the orchestration layer this repo already owns (`chunker/mcp_http_server.py`), matching how `_MCP_INSTRUCTIONS` and `_DEFAULT_CORPUS_PATH_PREFIX_CANDIDATES` already live there today as plain Python constants.
- **Alternatives considered**: Extend `data/.cocoindex_code/settings.yml`'s schema — rejected, forks vendored code for an unrelated concern. An env var per field (like issue #18's `BCATLAS_SOURCE_DIR`) — rejected: instructions text is multi-line prose and a prefix list is structured data, both awkward as env vars; a settings file colocated with the project (matching issue #18's Assumption that this follows the same per-directory mechanism) is a better fit and keeps all of a custom corpus's configuration (chunking rules, presentation) discoverable in one place under `project_root`.

## Decision: file location is `.bcatlas/mcp_presentation.yml`, not `.cocoindex_code/`

- **Decision**: A new `.bcatlas/` subdirectory under `project_root`, sibling to `.cocoindex_code/`.
- **Rationale**: Keeps a clean ownership boundary — anything under `.cocoindex_code/` is cocoindex-code's own state/config (including files it might rewrite via its own CLI, e.g. `ccc init`); `.bcatlas/` is unambiguously this repo's own config surface, safe from ever being touched by an upstream cocoindex-code change.

## Decision: parsing library — `yaml` (already a transitive dependency)

- **Investigated**: `cocoindex_code.settings` already uses `import yaml as _yaml` (confirmed in `tools/cocoindex-code/src/cocoindex_code/settings.py`), so PyYAML is already present in the same venv `chunker/mcp_http_server.py` runs in (`uv run --project tools/cocoindex-code --with-editable chunker`). No new dependency needed.
- **Decision**: Use `yaml.safe_load` directly in `chunker/mcp_http_server.py` for the new file — a small, self-contained loader function, not a new dataclass/schema layer (the shape is two optional fields, not worth a dedicated settings module).

## Decision: validation behavior (FR-005)

- **Decision**: `_load_presentation_settings(project_root)` (new function) raises `SystemExit` with a clear message (file path + parse error) if the file exists but fails to parse as YAML, or parses to something that isn't a mapping, or has an `instructions` key that isn't a string, or a `path_prefixes` key that isn't a list of strings. If the file doesn't exist at all, returns the all-defaults case silently (no file is not an error — only a *present but broken* file is).
- **Rationale**: Matches the fail-fast pattern already established for `_validate_project_root` in issue #18 (same file, same session) — a clear startup error beats confusing runtime behavior, consistent precedent within this codebase.

## Non-goals confirmed by this research

- No change to the multi-tenant registry/build pipeline's per-(country, version) serving — this only affects the single default-corpus-serving `chunker/mcp_http_server.py` process (same one issue #18 configures via `BCATLAS_SOURCE_DIR`).
- No hosted-instance deploy/restart needed to implement or verify this — the hosted VM's `data/` project root has no `.bcatlas/mcp_presentation.yml`, so its behavior is unchanged by construction; verification is local-only.
