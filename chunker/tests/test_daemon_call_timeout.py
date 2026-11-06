"""Unit tests for the deadline on daemon calls (`bcatlas_search`'s
`timeout_seconds`).

Live incident (2026-11-06, hosted VM): one search against a (country,
version) the daemon had never loaded triggered a first-time index that ran
for 7 days. The search waited on it forever while holding
`_DAEMON_CALL_LOCK`, so every other search queued behind it and also never
returned. These tests cover `_run_with_stall_recovery`'s deadline handling
with a fake blocking call -- no real daemon.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_http_server as server  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # The real progress signal reads on-disk index state and /proc.
    monkeypatch.setattr(server, "_progress_signal", lambda root: (0, 0.0, (), None))
    monkeypatch.setattr(server, "_abandoned_calls", {})
    monkeypatch.setattr(server, "_STALL_POLL_INTERVAL_S", 0.05)
    release = threading.Event()
    yield release
    release.set()  # unblock abandoned fake calls so their threads exit


def _blocking_call(release: threading.Event):
    def fn():
        release.wait(timeout=10)
        return "late"

    return fn


def test_call_finishing_before_deadline_returns_its_result() -> None:
    result = server._run_with_stall_recovery(
        lambda: "ok", "/proj", time.monotonic() + 5, call_kind="search"
    )
    assert result == "ok"


def test_call_past_deadline_raises_and_releases_lock(_isolate) -> None:
    started = time.monotonic()
    with pytest.raises(server.DaemonCallTimeout):
        server._run_with_stall_recovery(
            _blocking_call(_isolate), "/slow", time.monotonic() + 0.2, call_kind="search"
        )
    assert time.monotonic() - started < 2
    # The lock must be free again so other projects can be searched.
    assert server._DAEMON_CALL_LOCK.acquire(timeout=0.5)
    server._DAEMON_CALL_LOCK.release()


def test_same_project_fails_fast_while_abandoned_call_is_alive(_isolate) -> None:
    with pytest.raises(server.DaemonCallTimeout):
        server._run_with_stall_recovery(
            _blocking_call(_isolate), "/slow", time.monotonic() + 0.1, call_kind="search"
        )
    started = time.monotonic()
    with pytest.raises(server.DaemonCallTimeout):
        server._run_with_stall_recovery(
            lambda: "ok", "/slow", time.monotonic() + 5, call_kind="search"
        )
    assert time.monotonic() - started < 0.5


def test_other_project_and_other_call_kind_still_work(_isolate) -> None:
    with pytest.raises(server.DaemonCallTimeout):
        server._run_with_stall_recovery(
            _blocking_call(_isolate), "/slow", time.monotonic() + 0.1, call_kind="index"
        )
    deadline = time.monotonic() + 5
    assert server._run_with_stall_recovery(lambda: "a", "/other", deadline, call_kind="search") == "a"
    # An abandoned index refresh must not block searches on the same corpus.
    assert server._run_with_stall_recovery(lambda: "b", "/slow", deadline, call_kind="search") == "b"


def test_same_project_works_again_once_abandoned_call_finishes(_isolate) -> None:
    with pytest.raises(server.DaemonCallTimeout):
        server._run_with_stall_recovery(
            _blocking_call(_isolate), "/slow", time.monotonic() + 0.1, call_kind="search"
        )
    _isolate.set()
    time.sleep(0.1)
    result = server._run_with_stall_recovery(
        lambda: "ok", "/slow", time.monotonic() + 5, call_kind="search"
    )
    assert result == "ok"


def test_waiting_for_the_lock_respects_the_deadline() -> None:
    assert server._DAEMON_CALL_LOCK.acquire()
    try:
        started = time.monotonic()
        with pytest.raises(server.DaemonCallTimeout):
            server._run_with_stall_recovery(
                lambda: "ok", "/proj", time.monotonic() + 0.2, call_kind="search"
            )
        assert time.monotonic() - started < 1
    finally:
        server._DAEMON_CALL_LOCK.release()


def test_search_tool_returns_timeout_message_instead_of_hanging(
    _isolate, monkeypatch, tmp_path
) -> None:
    """End to end through the real `bcatlas_search` tool, with the daemon
    client faked to never answer."""
    import asyncio

    from cocoindex_code import client as daemon_client

    def never_answers(**_kwargs):
        _isolate.wait(timeout=10)
        raise RuntimeError("unreachable in this test")

    monkeypatch.setattr(daemon_client, "search", never_answers)
    (tmp_path / "a.al").write_text("codeunit 50100 X {}\n")
    mcp = server.create_filtered_mcp_server(str(tmp_path))

    async def call():
        return await mcp.call_tool(
            "bcatlas_search",
            {"query": "anything", "refresh_index": False, "timeout_seconds": 1},
        )

    started = time.monotonic()
    result = asyncio.run(call())
    elapsed = time.monotonic() - started
    text = str(result)
    assert elapsed < 5
    assert "timed out after 1s" in text
    assert "default corpus" in text
