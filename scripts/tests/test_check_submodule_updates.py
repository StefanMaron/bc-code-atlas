"""Unit tests for the pure decision/naming logic, plus real network-dependent
integration tests against the actual three watched upstream mirrors
(constitution Principle V -- measure against real upstream, not a mock, same
convention as registry/tests/test_resolver.py).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import check_submodule_updates as csu

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _init_repo_with_gitlink(root: Path, submodule_path: str, initial_sha: str) -> Path:
    """Sets up `root` as a real clone of a real bare `origin` remote (needed
    since bump_submodule_pointer fetches/branches off `origin/<BASE_BRANCH>`),
    with one committed gitlink for `submodule_path`. Returns the bare
    origin's path.
    """
    origin = root.parent / f"{root.name}-origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(root)], check=True)
    # Local repo config (not just -c flags per-call) so later commits made by
    # bump_submodule_pointer's own `_run` calls -- which don't pass -c --
    # succeed even on a runner with no global git identity configured.
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-q", "-m", "init"], cwd=root, check=True)
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{initial_sha},{submodule_path}"],
        cwd=root, check=True,
    )
    subprocess.run(["git", "commit", "-q", "-m", "add gitlink"], cwd=root, check=True)
    subprocess.run(["git", "push", "-q", "origin", "master"], cwd=root, check=True)
    return origin


def _gitlink_sha(root: Path, submodule_path: str) -> str:
    result = subprocess.run(
        ["git", "ls-tree", "HEAD", "--", submodule_path],
        cwd=root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip().split()[2]


# --- T009: plan_action ------------------------------------------------------

def test_plan_action_no_drift_is_none():
    assert csu.plan_action("abc123", "abc123", open_pr_exists=False) is csu.Action.NONE


def test_plan_action_drift_no_open_pr_is_open_pr():
    assert csu.plan_action("abc123", "def456", open_pr_exists=False) is csu.Action.OPEN_PR


def test_plan_action_drift_with_open_pr_is_none():
    # FR-004: don't duplicate a PR that already proposes this exact bump.
    assert csu.plan_action("abc123", "def456", open_pr_exists=True) is csu.Action.NONE


# --- T010: derive_branch_name ------------------------------------------------

def test_derive_branch_name_differs_by_target_sha():
    a = csu.derive_branch_name("data/w1-28-src", "111111111111111111111111111111111111aaaa")
    b = csu.derive_branch_name("data/w1-28-src", "222222222222222222222222222222222222bbbb")
    assert a != b


def test_derive_branch_name_is_deterministic():
    a = csu.derive_branch_name("data/docs", "111111111111111111111111111111111111aaaa")
    b = csu.derive_branch_name("data/docs", "111111111111111111111111111111111111aaaa")
    assert a == b


def test_derive_branch_name_shape():
    name = csu.derive_branch_name("data/docs-devitpro", "111111111111111111111111111111111111aaaa")
    assert name.startswith("bot/bump-data-docs-devitpro-")
    assert name.endswith("1111111")


# --- Regression: bump_submodule_pointer must not lose the gitlink change ---
# when the submodule's own content isn't checked out (`submodules: false`,
# this workflow's real posture) -- caught live in the first real
# workflow_dispatch run of this feature: `git commit -- <path>` re-derives
# the change from the working tree (which has no such path checked out) and
# silently commits a deletion instead of the staged gitlink bump.

def test_bump_submodule_pointer_commits_gitlink_not_deletion(tmp_path):
    submodule_path = "data/w1-28-src"
    old_sha = "111111111111111111111111111111111111aaaa"
    new_sha = "222222222222222222222222222222222222bbbb"
    repo = tmp_path / "repo"
    _init_repo_with_gitlink(repo, submodule_path, old_sha)

    submodule = csu.WatchedSubmodule(path=submodule_path, upstream_url="unused", branch="unused")
    csu.bump_submodule_pointer(submodule, new_sha, "bot/bump-test", repo_root=repo)

    assert _gitlink_sha(repo, submodule_path) == new_sha


# --- Regression: bumping two submodules in one run must not bundle them ----
# (FR-003) -- caught live in this feature's first real multi-submodule run:
# `git checkout -B branch_name` (no explicit start point) branches off
# whatever's currently checked out, which after processing the first
# submodule is that submodule's own just-created bump branch, not the base
# branch -- so the second submodule's PR silently included the first
# submodule's change too.

def test_bump_submodule_pointer_branches_are_independent(tmp_path):
    sub_a_path, sub_b_path = "data/w1-28-src", "data/docs"
    sub_a_old, sub_b_old = "111111111111111111111111111111111111aaaa", "444444444444444444444444444444444444dddd"
    sub_a_new, sub_b_new = "222222222222222222222222222222222222bbbb", "555555555555555555555555555555555555eeee"
    repo = tmp_path / "repo"
    origin = _init_repo_with_gitlink(repo, sub_a_path, sub_a_old)
    # Second submodule's initial gitlink, committed and pushed the same way.
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{sub_b_old},{sub_b_path}"],
        cwd=repo, check=True,
    )
    subprocess.run(["git", "commit", "-q", "-m", "add second gitlink"], cwd=repo, check=True)
    subprocess.run(["git", "push", "-q", "origin", "master"], cwd=repo, check=True)

    sub_a = csu.WatchedSubmodule(path=sub_a_path, upstream_url="unused", branch="unused")
    sub_b = csu.WatchedSubmodule(path=sub_b_path, upstream_url="unused", branch="unused")

    # Simulate main()'s loop: bump A, then (without resetting to origin/master
    # by hand -- that's exactly what bump_submodule_pointer must do itself)
    # bump B on a second branch in the same working copy.
    csu.bump_submodule_pointer(sub_a, sub_a_new, "bot/bump-a", repo_root=repo)
    csu.bump_submodule_pointer(sub_b, sub_b_new, "bot/bump-b", repo_root=repo)

    files_changed = subprocess.run(
        ["git", "diff", "--name-only", "master", "bot/bump-b"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert files_changed == [sub_b_path]


# --- T019: per-submodule independence ---------------------------------------

def test_watched_submodules_cover_exactly_the_three_in_scope():
    paths = {s.path for s in csu.WATCHED_SUBMODULES}
    assert paths == {"data/w1-28-src", "data/docs", "data/docs-devitpro"}
    # tools/graphify-al and other tooling submodules are explicitly out of
    # scope (spec.md Assumptions) -- must never be included here.
    assert "tools/graphify-al" not in paths


def test_each_watched_submodule_gets_its_own_branch_name():
    # FR-003: never bundle two submodules into one PR/branch.
    target_sha = "333333333333333333333333333333333333cccc"
    names = {csu.derive_branch_name(s.path, target_sha) for s in csu.WATCHED_SUBMODULES}
    assert len(names) == len(csu.WATCHED_SUBMODULES)


# --- T020/T021: safety-gate static checks -----------------------------------

def test_script_never_calls_gh_pr_merge():
    source = Path(csu.__file__).read_text()
    assert "pr merge" not in source
    assert "\"merge\"" not in source.replace("--force-with-lease", "")


def test_workflow_permissions_grant_no_merge_scope():
    workflow_path = _REPO_ROOT / ".github" / "workflows" / "submodule-watch.yml"
    text = workflow_path.read_text()
    assert "permissions:" in text
    assert "contents: write" in text
    assert "pull-requests: write" in text
    for forbidden in ("contents: admin", "administration: write", "actions: write"):
        assert forbidden not in text


# --- T011/T017: real, network-dependent checks against the real mirrors ----

pytestmark_network = pytest.mark.network


@pytest.mark.network
@pytest.mark.parametrize(
    "submodule",
    csu.WATCHED_SUBMODULES,
    ids=[s.path for s in csu.WATCHED_SUBMODULES],
)
def test_get_upstream_tip_sha_returns_real_sha(submodule):
    sha = csu.get_upstream_tip_sha(submodule.upstream_url, submodule.branch)
    assert len(sha) == 40
    assert all(c in "0123456789abcdef" for c in sha)


@pytest.mark.network
@pytest.mark.parametrize(
    "submodule",
    csu.WATCHED_SUBMODULES,
    ids=[s.path for s in csu.WATCHED_SUBMODULES],
)
def test_get_pinned_sha_matches_real_ls_tree(submodule):
    # Avoid a tautology (both paths calling the same function under test) by
    # re-running the raw git command directly here rather than reusing
    # csu._run.
    result = subprocess.run(
        ["git", "ls-tree", "HEAD", "--", submodule.path],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = result.stdout.strip().split()[2]
    assert csu.get_pinned_sha(submodule.path) == expected
