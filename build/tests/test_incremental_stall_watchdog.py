"""Tests for `build.incremental._daemon_cpu_ticks`: the CPU-activity signal
added to the `ccc index` stall watchdog after live-reproducing a false stall
on a cold (never-before-built) country's build -- the isolated per-build
daemon was burning real CPU loading the embedding model and starting its
first embedding batch, writing nothing under `.cocoindex_code/` for over
five minutes, which the on-disk-fingerprint-only watchdog misread as the
real, unrecoverable upstream hang and killed prematurely, forever, since a
fresh daemon just repeats the same slow startup on every retry.
"""
from __future__ import annotations

import os
from pathlib import Path

from build.incremental import _daemon_cpu_ticks


def test_missing_pidfile_returns_none(tmp_path: Path) -> None:
    assert _daemon_cpu_ticks(tmp_path) is None


def test_pidfile_pointing_at_nonexistent_pid_returns_none(tmp_path: Path) -> None:
    (tmp_path / "daemon.pid").write_text("999999999")
    assert _daemon_cpu_ticks(tmp_path) is None


def test_pidfile_with_garbage_content_returns_none(tmp_path: Path) -> None:
    (tmp_path / "daemon.pid").write_text("not-a-pid")
    assert _daemon_cpu_ticks(tmp_path) is None


def test_pidfile_pointing_at_a_real_process_returns_positive_ticks(tmp_path: Path) -> None:
    # Our own process is guaranteed to be running and to have accrued some
    # CPU time just by getting this far -- a real /proc read, not a mock
    # (constitution Principle V).
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))
    ticks = _daemon_cpu_ticks(tmp_path)
    assert ticks is not None
    assert ticks >= 0
