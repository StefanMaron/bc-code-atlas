# Quickstart: Configurable MCP Instructions and Path Filtering

Local-only validation — does not touch the hosted VM or its `data/` corpus.

## Prerequisites

- Same scratch AL directory setup as specs/005-local-source-directory/quickstart.md (steps 1-2), or reuse the default `data/` corpus for testing default-preservation (steps 4-5 below).

## 1. Configure custom instructions and path prefixes

```bash
mkdir -p /tmp/bcatlas-custom-al-test/.bcatlas
cat > /tmp/bcatlas-custom-al-test/.bcatlas/mcp_presentation.yml <<'EOF'
instructions: |
  Semantic search over a custom AL project (not Microsoft Business Central).
path_prefixes:
  - src
EOF
```

## 2. Start the server against the custom directory

```bash
BCATLAS_SOURCE_DIR=/tmp/bcatlas-custom-al-test SEARCH_PORT=8901 ./scripts/start-search-server.sh
```

## 3. Validate FR-001 — custom instructions are served

From an MCP client connected to `http://127.0.0.1:8901/mcp`, inspect the server's reported `instructions`. Expect the custom text from step 1, not the default Business Central text.

## 4. Validate FR-002 — default instructions unchanged when unconfigured

```bash
./scripts/start-search-server.sh   # BCATLAS_SOURCE_DIR unset, no .bcatlas/ under data/
```

Connect an MCP client and confirm `instructions` is exactly the existing default Business Central text.

## 5. Validate FR-005 — malformed settings file fails fast

```bash
mkdir -p /tmp/bcatlas-bad-settings/.bcatlas
echo "instructions: [this is not a string" > /tmp/bcatlas-bad-settings/.bcatlas/mcp_presentation.yml
mkdir -p /tmp/bcatlas-bad-settings-src && cp /tmp/bcatlas-custom-al-test/*.al /tmp/bcatlas-bad-settings-src/ 2>/dev/null || true
BCATLAS_SOURCE_DIR=/tmp/bcatlas-bad-settings SEARCH_PORT=8903 ./scripts/start-search-server.sh
```

Expected: process exits immediately with an error naming `.bcatlas/mcp_presentation.yml` and the parse problem; does not bind the port.

## Cleanup

```bash
rm -rf /tmp/bcatlas-custom-al-test /tmp/bcatlas-bad-settings /tmp/bcatlas-bad-settings-src
```
