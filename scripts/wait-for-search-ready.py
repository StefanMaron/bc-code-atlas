#!/usr/bin/env python3
"""Block until the local search backend has finished warming up after a
(re)start, and fail loudly if it never does.

Why this exists: `chunker/chunking.py`'s `CHUNKER_REGISTRY` docstring
documents (and this session traced and confirmed via
`cocoindex/_internal/memo_fingerprint.py`) that cocoindex-code cannot
memoize its custom AL chunker across a daemon restart -- every fresh
daemon (i.e. every deploy, since `deploy-vm.sh` restarts `bcatlas.target`)
pays a genuine ~30+ minute full reprocess of the whole corpus on its
*first* search/index call, upstream-by-design (constitution Principle VI,
not something to patch). Without this wait, `deploy-vm.sh` used to report
"deploy complete" the instant `systemctl restart` returned, while real
user queries hitting the still-cold daemon paid that cost inline (and,
before a companion fix in `mcp_http_server.py`, could get caught in an
watchdog restart loop that never finished at all). This makes that cost
visible in the deploy log instead of silently landing on the first live
user after every deploy.

Issues one real `bcatlas_search` call against the local chunker server
(not the aggregator, to avoid coupling this wait to any other backend's
own startup) and blocks until it succeeds -- the same call path, and the
same `refresh_index=True` default, that a real MCP client would use.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

SEARCH_URL = "http://127.0.0.1:8801/mcp"
# Generous: this call's own request can legitimately take 30-60+ minutes
# on a cold daemon (see module docstring). Connection-refused retries
# (server process still starting) use a much shorter budget below.
REQUEST_TIMEOUT_S = 3600.0
CONNECT_RETRY_TIMEOUT_S = 120.0
CONNECT_RETRY_INTERVAL_S = 2.0


def _mcp_post(session: dict, method: str, params: dict) -> dict | None:
    body = json.dumps({"jsonrpc": "2.0", "id": int(time.time() * 1000), "method": method, "params": params}).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session.get("id"):
        headers["Mcp-Session-Id"] = session["id"]
    req = urllib.request.Request(SEARCH_URL, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            session["id"] = sid
        text = resp.read().decode()
    line = next((l for l in text.split("\n") if l.startswith("data:")), None)
    raw = line[5:].strip() if line else text.strip()
    return json.loads(raw) if raw else None


def _wait_for_port() -> None:
    deadline = time.monotonic() + CONNECT_RETRY_TIMEOUT_S
    while True:
        try:
            urllib.request.urlopen(urllib.request.Request(SEARCH_URL, method="GET"), timeout=5)
            return
        except urllib.error.HTTPError:
            return  # server is up and rejecting GET -- that's fine, it's alive
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"search server never came up on {SEARCH_URL} within "
                    f"{CONNECT_RETRY_TIMEOUT_S:.0f}s of the service restart"
                ) from None
            time.sleep(CONNECT_RETRY_INTERVAL_S)


def main() -> int:
    print(f"waiting for {SEARCH_URL} to accept connections...", flush=True)
    _wait_for_port()

    print(
        "search server is up -- issuing a warm-up search (may take 30+ minutes on a "
        "fresh daemon, see chunker/chunking.py's CHUNKER_REGISTRY comment)...",
        flush=True,
    )
    start = time.monotonic()
    session: dict = {}
    _mcp_post(
        session,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "wait-for-search-ready", "version": "1.0.0"},
        },
    )
    result = _mcp_post(
        session,
        "tools/call",
        {"name": "bcatlas_search", "arguments": {"query": "deploy warm-up", "limit": 1}},
    )
    elapsed = time.monotonic() - start

    if result is None or result.get("error"):
        err = result.get("error") if result else "no response"
        print(f"warm-up search FAILED after {elapsed:.0f}s: {err}", file=sys.stderr, flush=True)
        return 1

    structured = (result.get("result") or {}).get("structuredContent")
    if isinstance(structured, dict) and structured.get("success") is False:
        print(
            f"warm-up search FAILED after {elapsed:.0f}s: {structured.get('message')}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(f"search index is warm -- ready in {elapsed:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
