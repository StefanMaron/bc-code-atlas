#!/usr/bin/env python3
"""Detect upstream advancement on the three data submodules backing the
always-warm default corpus (`data/w1-28-src`, `data/docs`,
`data/docs-devitpro`) and open a pull request bumping the pointer for each
one that has moved -- never merging anything (spec 008-reindex-webhook,
FR-005).

Design decisions and their rationale live in
specs/008-reindex-webhook/research.md; this module implements
contracts/detection-job.md's per-submodule check contract directly.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import subprocess
import sys
from enum import Enum
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclasses.dataclass(frozen=True)
class WatchedSubmodule:
    path: str
    upstream_url: str
    branch: str


# Fixed, explicit set -- deliberately NOT derived from .gitmodules wholesale.
# tools/graphify-al and other tooling submodules are out of scope for this
# feature (spec.md Assumptions: those are vendored forks with their own
# manual review/port workflow).
WATCHED_SUBMODULES: tuple[WatchedSubmodule, ...] = (
    WatchedSubmodule(
        path="data/w1-28-src",
        upstream_url="https://github.com/StefanMaron/MSDyn365BC.Sandbox.Code.History.git",
        branch="w1-28",
    ),
    WatchedSubmodule(
        path="data/docs",
        upstream_url="https://github.com/MicrosoftDocs/dynamics365smb-docs.git",
        branch="main",
    ),
    WatchedSubmodule(
        path="data/docs-devitpro",
        upstream_url="https://github.com/MicrosoftDocs/dynamics365smb-devitpro-pb.git",
        branch="main",
    ),
)


class GitOpsError(Exception):
    """A real git/gh subprocess failure (network, auth, ...)."""


class Action(Enum):
    NONE = "none"
    OPEN_PR = "open_pr"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise GitOpsError(
            f"{' '.join(args)} failed (exit {e.returncode}): {e.stderr.strip()}"
        ) from e
    except FileNotFoundError as e:
        raise GitOpsError(f"{args[0]} executable not found on PATH") from e


def get_pinned_sha(path: str, repo_root: Path = _REPO_ROOT) -> str:
    """The gitlink sha this repo's default branch currently has committed
    for `path`, read via `git ls-tree` -- no submodule checkout needed
    (research.md "Read the pinned commit via git ls-tree").
    """
    result = _run(["git", "ls-tree", "HEAD", "--", path], cwd=repo_root)
    line = result.stdout.strip()
    if not line:
        raise GitOpsError(f"no gitlink entry found for {path} at HEAD")
    # Format: "<mode> commit <sha>\t<path>"
    fields = line.split()
    return fields[2]


def get_upstream_tip_sha(upstream_url: str, branch: str) -> str:
    """The real, current tip of `branch` on `upstream_url`, via a single
    stateless `git ls-remote` call (research.md "Detect upstream advancement
    via git ls-remote" -- no local mirror needed, unlike registry's
    version-resolution machinery).
    """
    result = _run(["git", "ls-remote", upstream_url, f"refs/heads/{branch}"])
    line = result.stdout.strip()
    if not line:
        raise GitOpsError(f"branch {branch} not found on {upstream_url}")
    return line.split()[0]


def derive_branch_name(submodule_path: str, target_sha: str) -> str:
    """Deterministic per (submodule, target commit) branch name -- doubles
    as the duplicate-PR lookup key (research.md "Duplicate-PR guard via
    branch-name convention").
    """
    slug = submodule_path.replace("/", "-").replace("_", "-")
    short_sha = target_sha[:7]
    return f"bot/bump-{slug}-{short_sha}"


def plan_action(pinned_sha: str, upstream_tip_sha: str, open_pr_exists: bool) -> Action:
    """Pure decision function, no side effects -- matches
    contracts/detection-job.md's per-submodule check contract steps 3-4.
    """
    if pinned_sha == upstream_tip_sha:
        return Action.NONE
    if open_pr_exists:
        return Action.NONE
    return Action.OPEN_PR


def pr_exists_for_branch(branch_name: str, repo_root: Path = _REPO_ROOT) -> bool:
    """Whether an open PR already exists with head branch `branch_name`,
    via `gh pr list` (contracts/detection-job.md step 4).
    """
    result = _run(
        ["gh", "pr", "list", "--head", branch_name, "--state", "open", "--json", "number"],
        cwd=repo_root,
    )
    return result.stdout.strip() not in ("", "[]")


def bump_submodule_pointer(
    submodule: WatchedSubmodule,
    target_sha: str,
    branch_name: str,
    repo_root: Path = _REPO_ROOT,
) -> None:
    """Create `branch_name` off the default branch and commit a gitlink
    bump for `submodule.path` to `target_sha`, via `git update-index
    --cacheinfo` -- never checks out the submodule's own content
    (research.md "Bump the pointer via git update-index --cacheinfo").
    """
    _run(["git", "checkout", "-B", branch_name], cwd=repo_root)
    _run(
        ["git", "update-index", "--cacheinfo", f"160000,{target_sha},{submodule.path}"],
        cwd=repo_root,
    )
    # Deliberately no trailing pathspec here: `git commit -- <path>` re-adds
    # the path from the WORKING TREE before committing (like `git add -u
    # <path>`), which silently overwrites the gitlink bump just staged via
    # `update-index --cacheinfo` with a deletion, since the submodule's
    # content was never checked out (submodules: false) -- confirmed live
    # against a real run of this workflow. Committing with no pathspec
    # commits exactly what's staged in the index, which is the one change
    # made above.
    _run(
        ["git", "commit", "-m", f"[auto] Bump {submodule.path} to {target_sha[:7]}"],
        cwd=repo_root,
    )


def _pr_body(submodule: WatchedSubmodule, old_sha: str, target_sha: str) -> str:
    run_id = os.environ.get("GITHUB_RUN_ID")
    server_url = os.environ.get("GITHUB_SERVER_URL")
    repository = os.environ.get("GITHUB_REPOSITORY")
    run_line = ""
    if run_id and server_url and repository:
        run_line = f"\n\nOpened by {server_url}/{repository}/actions/runs/{run_id}."
    return (
        f"Automated bump of `{submodule.path}` from `{old_sha}` to `{target_sha}` "
        f"on upstream branch `{submodule.branch}` "
        f"({submodule.upstream_url}).{run_line}"
    )


def open_bump_pr(
    submodule: WatchedSubmodule,
    old_sha: str,
    target_sha: str,
    repo_root: Path = _REPO_ROOT,
) -> str:
    """Create the branch, bump the pointer, push, and open the PR
    (contracts/detection-job.md step 4). Returns the branch name used.
    """
    branch_name = derive_branch_name(submodule.path, target_sha)
    bump_submodule_pointer(submodule, target_sha, branch_name, repo_root)
    _run(["git", "push", "--force-with-lease", "origin", branch_name], cwd=repo_root)
    _run(
        [
            "gh",
            "pr",
            "create",
            "--title",
            f"[auto] Bump {submodule.path} to {target_sha[:7]}",
            "--body",
            _pr_body(submodule, old_sha, target_sha),
            "--head",
            branch_name,
            "--base",
            "master",
        ],
        cwd=repo_root,
    )
    return branch_name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would happen per submodule without creating branches/PRs.",
    )
    args = parser.parse_args(argv)

    exit_code = 0
    for submodule in WATCHED_SUBMODULES:
        try:
            pinned_sha = get_pinned_sha(submodule.path)
            upstream_tip_sha = get_upstream_tip_sha(submodule.upstream_url, submodule.branch)
        except GitOpsError as e:
            # One unreachable mirror must not block the others (FR-008).
            print(f"{submodule.path}: SKIPPED (unreachable: {e})", file=sys.stderr)
            exit_code = 1
            continue

        if pinned_sha == upstream_tip_sha:
            print(f"{submodule.path}: pinned={pinned_sha} upstream={upstream_tip_sha} action=none")
            continue

        branch_name = derive_branch_name(submodule.path, upstream_tip_sha)
        open_pr_exists = pr_exists_for_branch(branch_name) if not args.dry_run else False
        action = plan_action(pinned_sha, upstream_tip_sha, open_pr_exists)

        if action is Action.NONE:
            print(
                f"{submodule.path}: pinned={pinned_sha} upstream={upstream_tip_sha} "
                f"action=none (open PR already proposes {upstream_tip_sha})"
            )
            continue

        if args.dry_run:
            print(
                f"{submodule.path}: pinned={pinned_sha} upstream={upstream_tip_sha} "
                f"action=would-open-pr"
            )
            continue

        open_bump_pr(submodule, pinned_sha, upstream_tip_sha)
        print(f"{submodule.path}: pinned={pinned_sha} upstream={upstream_tip_sha} action=opened-pr")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
