# Research: Configurable Local AL Source Directory

## Key finding: the hard part doesn't need building — `mcp_http_server.py` already takes `project_root` as a parameter

`chunker/mcp_http_server.py:main()` already accepts `project_root` as a **positional CLI argument**, not a hardcoded path:

```python
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root")
    ...
    mcp_server = create_filtered_mcp_server(args.project_root)
```

The Microsoft-specific default (`data/`) is baked in at exactly one place: `scripts/start-search-server.sh`'s `exec uv run ... python "$ROOT/chunker/mcp_http_server.py" "$ROOT/data" ...`. That is the one and only hardcode that FR-001 needs to remove.

`_DEFAULT_CORPUS_PATH_PREFIX_CANDIDATES = ("w1-28-src", "docs", "docs-devitpro")` (`chunker/mcp_http_server.py:476`) is **not** a hardcode in the problematic sense — `_resolve_corpus_path_prefixes()` already computes, per `project_root`, which of these candidate subdirectories actually exist there (verified: it's a filesystem check, not an assumption). Pointed at a custom directory with none of these subdirs, it degrades cleanly to an empty tuple (no path-prefix expansion attempted) — correct behavior with zero changes needed.

## Decision: config surface is an environment variable read by the start script, not a chunker code change

- **Decision**: Add `BCATLAS_SOURCE_DIR` as an optional environment variable. `scripts/start-search-server.sh` uses it in place of `"$ROOT/data"` when set; falls back to `$ROOT/data` unchanged otherwise (FR-002).
- **Rationale**: Matches the existing pattern in this script for other overridable settings (`SEARCH_HOST`, `SEARCH_PORT` already work this way). No Python code changes needed in `mcp_http_server.py` beyond startup validation (below) — the parameter is already there.
- **Alternatives considered**: A new CLI flag on `mcp_http_server.py` itself — rejected, redundant with the positional arg that already exists; an env var at the wrapper-script layer is the minimal change and keeps `mcp_http_server.py` itself flag-compatible with how cocoindex-code's own docs describe invoking it.

## Decision: startup validation belongs in `mcp_http_server.py`, not the shell script

- **Decision**: Add an explicit check at the top of `main()` (or `create_filtered_mcp_server`) that `Path(project_root)` exists and is a directory, raising a clear, actionable error otherwise (FR-004); and a warning (not a failure) logged if no `.al` files are found under it (FR-005).
- **Rationale**: `create_filtered_mcp_server` today calls straight into `cocoindex_code.client`/`FastMCP` construction with no existence check — a typo'd path currently doesn't fail until (or unless) something later tries to touch the filesystem, which is exactly the "silently empty index" failure mode FR-004/FR-005 exist to prevent. Doing this in Python (not the bash wrapper) means it's enforced regardless of how the server is launched (directly, via the wrapper script, or via a future systemd unit).
- **Alternatives considered**: Validate in the bash wrapper with `[ -d ... ]` — rejected, doesn't cover direct invocations of `mcp_http_server.py` (e.g. local dev, tests) and duplicates logic that belongs in one place.

## Decision: a custom directory needs its own AL-scoped `.cocoindex_code/settings.yml`, and this repo should provide a template for it

- **Investigated**: `tools/cocoindex-code/src/cocoindex_code/settings.py`'s `ProjectSettings` defaults (`DEFAULT_INCLUDED_PATTERNS`) are a broad multi-language list (Python, JS, Rust, Go, ...) with an **empty** `chunkers` list by default — i.e., a brand-new project directory that has never been `ccc init`-ed (or has no settings.yml) would not automatically get AL-aware chunking; `.al` files would either be skipped (not in the generic pattern list — confirmed `.al` is absent from `DEFAULT_INCLUDED_PATTERNS`) or, if added, chunked generically instead of via `al_chunker`.
- **Decision**: Ship a minimal AL-focused settings template (`chunker/templates/al-source-settings.yml`, containing just `include_patterns: ["**/*.al"]` and `chunkers: [{ext: al, module: al_chunker:al_chunker}]` — deliberately without the BC-specific `Tests-*` exclude list from `data/.cocoindex_code/settings.yml`, since that list is a Microsoft-corpus-specific optimization, not a general AL-project property) and document the one-time setup: `ccc init` in the custom directory, then copy the template to `<custom-dir>/.cocoindex_code/settings.yml` (or merge if the operator wants their own excludes too).
- **Rationale**: This is genuinely required for FR-003 ("index only `.al` files... independent of Microsoft-specific layout assumptions") to produce AL-aware chunking rather than falling back to generic/no chunking. Keeping the template outside `data/` (which stays Microsoft-corpus-specific) avoids coupling the two.
- **Alternatives considered**: Auto-write the settings.yml programmatically from `mcp_http_server.py` on first run — rejected as more invasive (silently mutates operator-owned directory state) and inconsistent with `ccc init`'s existing ownership of that file; a documented template + one manual step is simpler and matches how the default corpus's own `settings.yml` was hand-authored in the first place.

## Decision: relative result paths (FR-006) need no change

- **Investigated**: cocoindex-code indexes and reports paths relative to `project_root` already (this is how the existing default corpus's results show `w1-28-src/...`-prefixed paths — relative to `data/`, not absolute). Pointed at a different `project_root`, results are naturally relative to *that* root instead. No code change needed; this requirement is satisfied by the existing indexing behavior once `project_root` itself is configurable.

## Non-goals confirmed by this research (from spec Assumptions)

- No change to the registry-driven multi-country/multi-version build pipeline (`build/`, `registry/`, `data/warm/<country>/<version>/`) — a custom local directory is a separate, single-corpus mode of `chunker/mcp_http_server.py`'s own default-corpus slot, not a new kind of (country, version) pair.
- No hosted-instance deploy or restart as part of this feature — `BCATLAS_SOURCE_DIR` unset in the hosted VM's actual environment means zero behavior change there; verification is local-only (see quickstart.md).
