"""Real-filesystem tests for `build.eviction` (T026): oldest-last-accessed
ordering and in-flight-base-sibling protection, against real temp
directories with real mtimes.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from build import eviction, layout


def _make_warm_entry(data_dir: Path, country: str, version: str, size_bytes: int, mtime: float) -> Path:
    search_dir = layout.warm_search_dir(country, version, data_dir)
    search_dir.mkdir(parents=True)
    (search_dir / "blob.dat").write_bytes(b"x" * size_bytes)
    root = layout.warm_root(country, version, data_dir)
    # Set mtime on the root itself (that's what scan_warm_entries reads as
    # last_accessed_at) -- os.utime with follow_symlinks default is fine for
    # a real directory.
    os.utime(root, (mtime, mtime))
    return root


def test_scan_warm_entries_reads_real_size_and_mtime(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    now = time.time()
    _make_warm_entry(data_dir, "w1", "28.1", size_bytes=1000, mtime=now - 100)
    _make_warm_entry(data_dir, "us", "28.2", size_bytes=2000, mtime=now - 50)

    entries = eviction.scan_warm_entries(data_dir)
    by_key = {(e.country, e.version): e for e in entries}

    assert len(entries) == 2
    assert by_key[("w1", "28.1")].size_bytes == 1000
    assert by_key[("us", "28.2")].size_bytes == 2000
    assert by_key[("w1", "28.1")].last_accessed_at < by_key[("us", "28.2")].last_accessed_at


def test_scan_warm_entries_empty_when_no_warm_dir(tmp_path: Path) -> None:
    assert eviction.scan_warm_entries(tmp_path / "nonexistent") == []


def test_evict_reclaims_oldest_first_until_under_budget(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    now = time.time()
    # Three entries of 1000 bytes each, oldest to newest.
    _make_warm_entry(data_dir, "w1", "v1", size_bytes=1000, mtime=now - 300)
    _make_warm_entry(data_dir, "w1", "v2", size_bytes=1000, mtime=now - 200)
    _make_warm_entry(data_dir, "w1", "v3", size_bytes=1000, mtime=now - 100)

    # Budget of 1000 -- must reclaim exactly the two oldest to get under it.
    removed = eviction.evict(data_dir=data_dir, budget_bytes=1000)
    removed_keys = [(e.country, e.version) for e in removed]

    assert removed_keys == [("w1", "v1"), ("w1", "v2")]
    remaining = {(e.country, e.version) for e in eviction.scan_warm_entries(data_dir)}
    assert remaining == {("w1", "v3")}
    assert not layout.warm_root("w1", "v1", data_dir).exists()
    assert not layout.warm_root("w1", "v2", data_dir).exists()
    assert layout.warm_root("w1", "v3", data_dir).exists()


def test_evict_no_op_when_already_under_budget(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _make_warm_entry(data_dir, "w1", "v1", size_bytes=1000, mtime=time.time())

    removed = eviction.evict(data_dir=data_dir, budget_bytes=10_000)

    assert removed == []
    assert layout.warm_root("w1", "v1", data_dir).exists()


def test_evict_skips_protected_in_flight_base_sibling(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    now = time.time()
    # Oldest entry is protected (an in-flight build's base sibling) -- it
    # must survive even though it would normally be reclaimed first, and
    # eviction must instead reach further down the LRU order to make budget.
    _make_warm_entry(data_dir, "w1", "protected", size_bytes=1000, mtime=now - 300)
    _make_warm_entry(data_dir, "w1", "v2", size_bytes=1000, mtime=now - 200)
    _make_warm_entry(data_dir, "w1", "v3", size_bytes=1000, mtime=now - 100)

    removed = eviction.evict(
        data_dir=data_dir,
        budget_bytes=1000,
        protected=frozenset({("w1", "protected")}),
    )
    removed_keys = {(e.country, e.version) for e in removed}

    # Total is 3000 bytes over a 1000-byte budget: protected (1000) can
    # never be reclaimed, so both non-protected entries (v2, v3) must be
    # removed to get as close to budget as achievable -- eviction must not
    # stop early just because *an* entry was skipped, and must not
    # loop/crash when the budget is structurally unreachable (the
    # protected entry alone already equals the budget).
    assert ("w1", "protected") not in removed_keys
    assert removed_keys == {("w1", "v2"), ("w1", "v3")}
    assert layout.warm_root("w1", "protected", data_dir).exists()
    assert not layout.warm_root("w1", "v2", data_dir).exists()
    assert not layout.warm_root("w1", "v3", data_dir).exists()


def test_evict_all_entries_protected_removes_nothing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _make_warm_entry(data_dir, "w1", "v1", size_bytes=5000, mtime=time.time())

    removed = eviction.evict(
        data_dir=data_dir,
        budget_bytes=0,
        protected=frozenset({("w1", "v1")}),
    )

    assert removed == []
    assert layout.warm_root("w1", "v1", data_dir).exists()


def test_evicted_version_is_rebuildable_afterward(tmp_path: Path) -> None:
    """Constitution Principle III: eviction is always safe to reverse by
    rebuilding -- this test only proves the mechanical half (the reclaimed
    warm path is fully gone and a fresh `warm_root` for the same key can be
    recreated from scratch), not the actual rebuild (that's incremental.py).
    """
    data_dir = tmp_path / "data"
    _make_warm_entry(data_dir, "w1", "v1", size_bytes=1000, mtime=time.time())

    eviction.evict(data_dir=data_dir, budget_bytes=0)
    assert not layout.warm_root("w1", "v1", data_dir).exists()

    # Rebuilding just means the path is free to be recreated -- prove it's
    # not left in some half-deleted/locked state.
    recreated = layout.warm_search_dir("w1", "v1", data_dir)
    recreated.mkdir(parents=True)
    assert recreated.is_dir()
