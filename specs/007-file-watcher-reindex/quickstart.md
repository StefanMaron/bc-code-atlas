# Quickstart: Optional Continuous Re-Index (Watch Mode)

Local-only validation — does not touch the hosted VM or its `data/` corpus.

## Prerequisites

- Same scratch AL directory setup as specs/005-local-source-directory/quickstart.md (steps 1-2).

## 1. Start the server with watch mode enabled

```bash
BCATLAS_SOURCE_DIR=/tmp/bcatlas-custom-al-test \
BCATLAS_WATCH_INTERVAL_SECONDS=2 \
SEARCH_PORT=8905 \
./scripts/start-search-server.sh
```

Expected: starts normally, no error (a positive interval is valid).

## 2. Validate FR-001 / FR-003 — a new file becomes searchable without an explicit refresh

While the server from step 1 is running, in another terminal:

```bash
cat > /tmp/bcatlas-custom-al-test/GoodbyeWorld.al <<'EOF'
codeunit 50101 "Goodbye World"
{
    procedure SayGoodbye()
    begin
        Message('Goodbye from watch mode');
    end;
}
EOF
```

Wait ~5 seconds (more than the 2s interval), then call `bcatlas_search` with `refresh_index=false` and query `"say goodbye"`. Expected: `GoodbyeWorld.al` is found even though the search call itself did not request a refresh — watch mode already indexed it in the background.

## 3. Validate FR-002 — default behavior unchanged when unset

```bash
BCATLAS_SOURCE_DIR=/tmp/bcatlas-custom-al-test SEARCH_PORT=8906 ./scripts/start-search-server.sh
```

(no `BCATLAS_WATCH_INTERVAL_SECONDS`) — add a new file, then immediately search with `refresh_index=false`: expect it is NOT found (today's exact on-demand behavior), confirming watch mode did not silently activate.

## 4. Validate the fail-fast contract — invalid interval

```bash
BCATLAS_SOURCE_DIR=/tmp/bcatlas-custom-al-test BCATLAS_WATCH_INTERVAL_SECONDS=0 SEARCH_PORT=8907 ./scripts/start-search-server.sh
```

Expected: process exits immediately with an error naming the invalid interval; does not bind the port.

## Cleanup

```bash
rm -rf /tmp/bcatlas-custom-al-test
```

No hosted-VM step in this quickstart — intentional (see spec Assumptions).
