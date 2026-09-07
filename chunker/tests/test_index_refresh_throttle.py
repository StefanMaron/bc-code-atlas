"""Unit tests for the `bcatlas_search` refresh-index throttle.

`refresh_index=True` (the search tool's default) used to re-run the
daemon's full incremental corpus scan on every single call -- measured live
(2026-09-07) at ~33s of a ~42s request even when nothing had changed, since
content-hashing every file across w1-28-src + docs + docs-devitpro isn't
free just because the result is `num_unchanged`. `_claim_index_refresh`
caps how often that scan actually runs; these tests cover its own
check-and-claim logic in isolation, not the real daemon scan (already
covered by test_daemon_persistence.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_http_server as server  # noqa: E402


def _reset(monkeypatch, *, min_interval: float = 300.0, last_refresh: float = 0.0) -> None:
    monkeypatch.setattr(server, "_INDEX_REFRESH_MIN_INTERVAL_S", min_interval)
    monkeypatch.setattr(server, "_last_index_refresh_at", last_refresh)


def test_first_call_is_due(monkeypatch) -> None:
    _reset(monkeypatch)
    assert server._claim_index_refresh() is True


def test_second_call_within_window_is_not_due(monkeypatch) -> None:
    _reset(monkeypatch)
    assert server._claim_index_refresh() is True
    assert server._claim_index_refresh() is False


def test_call_after_window_elapses_is_due_again(monkeypatch) -> None:
    import time

    _reset(monkeypatch, min_interval=0.01)
    assert server._claim_index_refresh() is True
    time.sleep(0.02)
    assert server._claim_index_refresh() is True


def test_mark_index_refreshed_starts_a_new_window(monkeypatch) -> None:
    _reset(monkeypatch)
    server._mark_index_refreshed()
    assert server._claim_index_refresh() is False
