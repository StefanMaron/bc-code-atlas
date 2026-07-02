"""Real, network-dependent tests of registry.git_ops against the actual
upstream repository (github.com/StefanMaron/MSDyn365BC.Sandbox.Code.History).

NETWORK-DEPENDENT: every test in this module makes real network calls to
GitHub. Per plan.md's Testing section and constitution Principle V ("Measure,
Don't Assume"), git_ops.py is deliberately verified against the real
upstream repo, not a local fixture/mock -- the whole point of this module is
git plumbing whose exact behavior (shallow-fetch depth semantics, ancestor
resolution across depth-limited history) was independently confirmed this
session to NOT match naive assumptions, so a mock would just re-encode
those wrong assumptions instead of catching them.

CI environments without network access should skip this module; the
`pytest.ini_options` marker below (`network`) makes that explicit and
filterable (`pytest -m "not network"`), without silently passing tests that
never actually ran.

Each test uses its own isolated tmp_path mirror directory so runs don't
interfere with each other or with any mirror a running registry server has
open elsewhere.
"""
from __future__ import annotations

import pytest

from registry import git_ops

pytestmark = pytest.mark.network

# Real, fixed commits on the real w1-28 branch, discovered live via
# `git ls-remote`/`git fetch --depth N` against the upstream repo during
# development of this module (not invented shas) -- see git_ops.py's module
# docstring for the exploration that established these. Kept as literal
# constants (rather than re-discovered per test run) so a test failure means
# git_ops.py broke, not that upstream branch tips moved.
_TO_SHA = "e94dbd8173ef42cfa4883983eb07c758b13c749f"  # w1-28.1.49838.51992
_FROM_SHA = "5d6549ea4a5c037b8032ab89b1cf673a18927a3a"  # w1-28.1.49838.51918, an ancestor of _TO_SHA
_TOUCHED_PATH = "Base Application/eServices/EDocument/ReportInbox.Page.al"
_OTHER_BRANCH_SHA = "c56c5ca618d6d5bfffba6b9c7228a638c1a03efc"  # at-25 tip -- unrelated history


def test_list_branches_finds_real_branches():
    branches = git_ops.list_branches()
    # 546 branches / ~51 countries observed directly against the real repo
    # this session (constitution's Technology & Data Constraints) -- assert
    # a lower bound, not an exact count, since new branches can appear.
    assert len(branches) >= 500
    assert "w1-28" in branches


def test_fetch_commit_and_read_blob_roundtrip(tmp_path):
    mirror = tmp_path / "mirror"
    git_ops.fetch_commit(_TO_SHA, mirror_dir=mirror)

    blob = git_ops.read_blob(_TO_SHA, _TOUCHED_PATH, mirror_dir=mirror)

    assert isinstance(blob, bytes)
    assert len(blob) > 0
    # Real AL source signature -- confirms this is genuine fetched content,
    # not an empty/error placeholder.
    assert b"page 680" in blob or b"Report Inbox" in blob


def test_fetch_commit_reuses_shared_mirror_not_per_call_clone(tmp_path):
    mirror = tmp_path / "mirror"
    git_ops.fetch_commit(_TO_SHA, mirror_dir=mirror)
    assert (mirror / "HEAD").is_file()  # bare repo initialized once

    # A second fetch of the same commit must be a no-op against the same
    # mirror (git_ops._object_exists short-circuits it) -- exercised
    # directly here as the observable contract: it must not raise and the
    # blob must still read correctly afterward.
    git_ops.fetch_commit(_TO_SHA, mirror_dir=mirror)
    blob = git_ops.read_blob(_TO_SHA, _TOUCHED_PATH, mirror_dir=mirror)
    assert len(blob) > 0


def test_read_blob_missing_path_raises_git_ops_error(tmp_path):
    mirror = tmp_path / "mirror"
    with pytest.raises(git_ops.GitOpsError):
        git_ops.read_blob(_TO_SHA, "this/path/does/not/exist.al", mirror_dir=mirror)


def test_log_for_path_returns_oldest_first_and_filters_by_path(tmp_path):
    mirror = tmp_path / "mirror"

    shas = git_ops.log_for_path(_TOUCHED_PATH, _FROM_SHA, _TO_SHA, mirror_dir=mirror)

    assert shas, "expected at least one commit touching the path in this real range"
    # NOTE: "touched the path" (what git log -- <path> reports) is NOT the
    # same guarantee as "the file's final content changed" -- confirmed for
    # real here: these two specific commits touch this exact path yet its
    # content at _FROM_SHA and _TO_SHA is byte-identical (e.g. a revert, or
    # a rebuild re-committing the same bytes). That's exactly why
    # history.py (a later task) re-extracts and diffs text per commit
    # rather than trusting `git log -- path` alone for change detection
    # (research.md's symbol-history decision, FR-008) -- not a bug here.
    from_blob = git_ops.read_blob(_FROM_SHA, _TOUCHED_PATH, mirror_dir=mirror)
    to_blob = git_ops.read_blob(_TO_SHA, _TOUCHED_PATH, mirror_dir=mirror)
    assert isinstance(from_blob, bytes) and isinstance(to_blob, bytes)

    # oldest-first: log_for_path documents this explicitly (opposite of
    # plain `git log`'s newest-first order).
    assert len(shas) == 2
    assert shas == ["69e72f609e0ddd24a62aab5ea85046c7834f41ad", "50cb0696c3b3bfb12eab52913d0bbe9d9c79e91d"]


def test_log_for_path_raises_value_error_for_unrelated_commits(tmp_path, monkeypatch):
    mirror = tmp_path / "mirror"
    # Cap the search depth so this test fails fast instead of walking
    # toward a full clone before giving up.
    monkeypatch.setattr(git_ops, "_MAX_LOG_DEPTH", 200)

    with pytest.raises(ValueError):
        git_ops.log_for_path(_TOUCHED_PATH, _OTHER_BRANCH_SHA, _TO_SHA, mirror_dir=mirror)
