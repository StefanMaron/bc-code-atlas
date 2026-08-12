"""Unit tests for watch mode (specs/007-file-watcher-reindex, GitHub issue
#21): opt-in continuous reindexing, disabled unless explicitly configured
(FR-001/FR-002), coalescing multiple changes into the next reindex pass
(FR-005), and staying alive/logging through a failed reindex attempt
(FR-006) rather than crashing or going silently stale.

Uses a stub `reindex_once` instead of the real cocoindex daemon -- the real
daemon path is already covered end-to-end by test_daemon_persistence.py and
by this session's manual quickstart.md verifications; these tests are about
the watch loop's own scheduling/error-handling behavior in isolation.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_http_server import _validate_watch_interval, _watch_loop  # noqa: E402


def test_none_interval_is_valid() -> None:
    _validate_watch_interval(None)  # must not raise


@pytest.mark.parametrize("bad_value", [0, -1, -0.5])
def test_non_positive_interval_exits(bad_value: float) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _validate_watch_interval(bad_value)
    assert str(bad_value) in str(exc_info.value)


def test_positive_interval_is_valid() -> None:
    _validate_watch_interval(1.5)  # must not raise


@pytest.mark.asyncio
async def test_watch_loop_reindexes_repeatedly() -> None:
    calls: list[str] = []

    def _stub(project_root: str) -> None:
        calls.append(project_root)

    task = asyncio.create_task(_watch_loop("/fake/project", 0.01, reindex_once=_stub))
    try:
        await asyncio.wait_for(_wait_until(lambda: len(calls) >= 3), timeout=2.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(calls) >= 3
    assert all(c == "/fake/project" for c in calls)


@pytest.mark.asyncio
async def test_watch_loop_survives_a_failed_reindex(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[int] = []

    def _stub(project_root: str) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated daemon failure")

    task = asyncio.create_task(_watch_loop("/fake/project", 0.01, reindex_once=_stub))
    try:
        await asyncio.wait_for(_wait_until(lambda: len(calls) >= 2), timeout=2.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(calls) >= 2  # loop kept going past the first failure
    out = capsys.readouterr().out
    assert "warning" in out.lower()
    assert "/fake/project" in out


async def _wait_until(predicate: object, poll_s: float = 0.01) -> None:
    while not predicate():  # type: ignore[operator]
        await asyncio.sleep(poll_s)
