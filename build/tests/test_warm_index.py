"""Real-filesystem tests for `build.warm_index.list_warm_versions`: sort
order and per-entry shape, against real temp directories with real mtimes.
`resolve_version_string` is injected as a stub so this stays a fast,
network-free test -- git_ops.commit_message itself is exercised for real
elsewhere (registry/tests/test_git_ops.py, per this project's convention of
never mocking git plumbing).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from build import layout
from build.warm_index import list_warm_versions


def _make_warm_entry(data_dir: Path, country: str, version: str, size_bytes: int, mtime: float) -> Path:
    search_dir = layout.warm_search_dir(country, version, data_dir)
    search_dir.mkdir(parents=True)
    (search_dir / "blob.dat").write_bytes(b"x" * size_bytes)
    root = layout.warm_root(country, version, data_dir)
    os.utime(root, (mtime, mtime))
    return root


def _fake_commit_message(sha: str, mirror_dir: Path, upstream_url: str) -> str:
    return f"w1-{sha}"


def test_list_warm_versions_sorts_newest_first_within_country(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    now = time.time()
    _make_warm_entry(data_dir, "w1", "sha-old", size_bytes=1000, mtime=now - 200)
    _make_warm_entry(data_dir, "w1", "sha-new", size_bytes=2000, mtime=now - 10)
    _make_warm_entry(data_dir, "us", "sha-us", size_bytes=500, mtime=now - 100)

    versions = list_warm_versions(data_dir=data_dir, resolve_version_string=_fake_commit_message)

    assert [(v["country"], v["commit_sha"]) for v in versions] == [
        ("us", "sha-us"),
        ("w1", "sha-new"),
        ("w1", "sha-old"),
    ]
    newest = versions[1]
    assert newest["version_string"] == "w1-sha-new"
    assert newest["size_bytes"] == 2000
    assert isinstance(newest["last_touched"], str) and newest["last_touched"].endswith("+00:00")


def test_list_warm_versions_filters_by_country(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    now = time.time()
    _make_warm_entry(data_dir, "w1", "sha-w1", size_bytes=1000, mtime=now)
    _make_warm_entry(data_dir, "us", "sha-us", size_bytes=1000, mtime=now)

    versions = list_warm_versions(data_dir=data_dir, country="w1", resolve_version_string=_fake_commit_message)

    assert len(versions) == 1
    assert versions[0]["country"] == "w1"


def test_list_warm_versions_survives_a_resolution_failure(tmp_path: Path) -> None:
    from registry import git_ops

    data_dir = tmp_path / "data"
    _make_warm_entry(data_dir, "w1", "sha-bad", size_bytes=1000, mtime=time.time())

    def _raises(sha: str, mirror_dir: Path, upstream_url: str) -> str:
        raise git_ops.GitOpsError("network unavailable")

    versions = list_warm_versions(data_dir=data_dir, resolve_version_string=_raises)

    assert len(versions) == 1
    assert versions[0]["version_string"] is None
