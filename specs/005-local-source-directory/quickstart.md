# Quickstart: Configurable Local AL Source Directory

Local-only validation — does not touch the hosted VM or its `data/` corpus.

## Prerequisites

- Repo checked out locally with `tools/cocoindex-code` and `chunker/` set up as usual (see main `README.md` Quick Start).

## 1. Create a scratch AL source directory

```bash
mkdir -p /tmp/bcatlas-custom-al-test
cat > /tmp/bcatlas-custom-al-test/HelloWorld.al <<'EOF'
codeunit 50100 "Hello World"
{
    procedure SayHello()
    begin
        Message('Hello from a custom local AL source directory');
    end;
}
EOF
```

## 2. Initialize it as a cocoindex-code project and apply the AL chunker template

```bash
cd /tmp/bcatlas-custom-al-test
uv run --project <repo>/tools/cocoindex-code ccc init
cp <repo>/chunker/templates/al-source-settings.yml .cocoindex_code/settings.yml
```

## 3. Start the search server against it

```bash
cd <repo>
BCATLAS_SOURCE_DIR=/tmp/bcatlas-custom-al-test SEARCH_PORT=8901 ./scripts/start-search-server.sh
```

Expected: server starts on port 8901 against `/tmp/bcatlas-custom-al-test` — confirm the startup log names that path, not `data/`.

## 4. Validate FR-001 / FR-003 / FR-006 — search finds the custom content

From a separate MCP client session pointed at `http://127.0.0.1:8901/mcp`, call `bcatlas_search` with query `"say hello"`. Expect a result from `HelloWorld.al`, path reported relative to the custom directory (not prefixed with `w1-28-src/`).

## 5. Validate FR-002 — default behavior is unchanged

```bash
./scripts/start-search-server.sh   # BCATLAS_SOURCE_DIR unset
```

Expected: starts against `<repo>/data` exactly as before this feature existed.

## 6. Validate FR-004 — fails fast on a bad path

```bash
BCATLAS_SOURCE_DIR=/tmp/does-not-exist ./scripts/start-search-server.sh
```

Expected: process exits immediately with an error naming `/tmp/does-not-exist`; does not bind the port.

## 7. Validate FR-005 — warns on an empty directory

```bash
mkdir -p /tmp/bcatlas-empty && BCATLAS_SOURCE_DIR=/tmp/bcatlas-empty SEARCH_PORT=8902 ./scripts/start-search-server.sh
```

Expected: starts (does not exit), but logs a clear warning that no `.al` files were found under `/tmp/bcatlas-empty`.

## Cleanup

```bash
rm -rf /tmp/bcatlas-custom-al-test /tmp/bcatlas-empty
```

No hosted-VM step in this quickstart — that is intentional (see spec Assumptions: hosted default corpus is untouched by this feature).
