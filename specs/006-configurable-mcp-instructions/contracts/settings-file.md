# Contract: `.bcatlas/mcp_presentation.yml`

## Location

`<project_root>/.bcatlas/mcp_presentation.yml`, where `project_root` is the same directory `chunker/mcp_http_server.py` is started against (the positional CLI arg / `BCATLAS_SOURCE_DIR`, per specs/005-local-source-directory).

## Schema

```yaml
# Both keys optional. Omit the file entirely for full default behavior.
instructions: |
  Multi-line MCP server instructions text, reported to connecting clients
  in place of the built-in Business Central description.
path_prefixes:
  - some-subdir
  - another-subdir
```

| Key | Type | Required | Default |
|---|---|---|---|
| `instructions` | string | no | built-in BC instructions text |
| `path_prefixes` | list of strings | no | dynamically detected default candidates (`w1-28-src`, `docs`, `docs-devitpro`) that exist under `project_root` |

## Startup contract

1. File absent → both fields use defaults, no error, no log message beyond normal startup (matches today's behavior exactly).
2. File present, valid, one or both keys present → those keys override their default; any omitted key still defaults.
3. File present but invalid (bad YAML, non-mapping top level, wrong field types) → process exits non-zero before binding the HTTP port, with a message naming the file path and the problem (FR-005).

## Non-contract (unchanged)

- MCP tool names, request/response schemas: unchanged (FR-006).
- Issue #18's `BCATLAS_SOURCE_DIR` / startup path validation: unrelated, independent, unaffected.
- The hosted production instance's `data/` project root has no `.bcatlas/` directory, so its served instructions and path-filtering behavior are unaffected by this feature (FR-007).
