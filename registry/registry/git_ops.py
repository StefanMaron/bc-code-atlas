"""Git plumbing against the real upstream source-history repository.

Every function here is a thin wrapper around a real `git` subprocess call --
no new Python git library, per plan.md's Technical Context ("git plumbing
commands are simple and already proven this session via direct shell use").

All operations after `list_branches` share ONE local bare mirror
(`DEFAULT_MIRROR_DIR`, `data/.upstream-mirror/` by default) rather than
cloning fresh per call/per commit -- this avoids redundant network fetches
for the same commit requested by multiple concurrent callers (research.md
"on-demand historical blob/commit fetch via scoped shallow fetches into a
shared mirror"). The mirror is a bare repo (no working tree needed -- every
read here is `git show`/`git log`, never a checkout).

Verified behavior worth documenting because it isn't obvious and changed
this module's design (constitution Principle V -- measure, don't assume):

- `git fetch origin <sha> --depth 1` against a commit that's already present
  in the mirror at a GREATER depth actually SHRINKS its local shallow
  history back down to 1 commit (confirmed directly against the real
  upstream repo this session). `fetch_commit` therefore checks whether the
  commit object already exists locally before ever issuing a depth-1 fetch,
  so a commit already deepened by `log_for_path` is never truncated by a
  later `fetch_commit`/`read_blob` call for the same sha.
- Each fetched commit is landed on its own persistent local ref
  (`refs/bcatlas/commits/<sha>`), not left to rely on the ephemeral
  `FETCH_HEAD` -- this is what makes re-fetching the same sha with a larger
  `--depth` actually deepen it (confirmed working) instead of being treated
  as a no-op, and keeps the commit reachable/safe from GC.
- `git fetch origin <sha> --shallow-exclude <other_sha>` (the seemingly
  obvious way to fetch exactly the range between two commits) fails against
  GitHub's real server for this repository ("fatal: expected 'packfile'",
  confirmed this session) -- `log_for_path` instead grows the shallow depth
  on `to_sha`'s ref by doubling until `from_sha` is a real ancestor of it
  (`git merge-base --is-ancestor`), which does work.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# Source of truth per constitution's Technology & Data Constraints.
UPSTREAM_URL = "https://github.com/StefanMaron/MSDyn365BC.Sandbox.Code.History.git"

# registry/registry/git_ops.py -> parents[2] is the repo root (registry/registry
# -> registry -> repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MIRROR_DIR = _REPO_ROOT / "data" / ".upstream-mirror"

# log_for_path's shallow-deepening search: start small (most path-scoped
# ranges are far shorter than a full branch's history -- research.md
# measured one full minor-version span at 99 build commits), double on each
# miss, give up past this cap rather than looping toward a full clone.
_INITIAL_LOG_DEPTH = 50
_MAX_LOG_DEPTH = 20_000


class GitOpsError(Exception):
    """Raised when a git subprocess call fails for a reason other than an
    expected/handled case (e.g. a real network/auth failure, a malformed
    sha). NOT raised for from_sha/to_sha not being connected within
    `_MAX_LOG_DEPTH` -- that raises ValueError instead, since it's a caller
    input problem, not a git-plumbing failure.
    """


def _run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise GitOpsError(
            f"git {' '.join(args)} failed (exit {e.returncode}): {e.stderr.strip()}"
        ) from e
    except FileNotFoundError as e:
        raise GitOpsError("git executable not found on PATH") from e


def list_branches(upstream_url: str = UPSTREAM_URL) -> list[str]:
    """Real `git ls-remote --heads <upstream_url>`, parsed to branch names.

    No mirror/fetch involved -- this is a single stateless network call,
    always current (research.md: "querying git directly ... never can
    drift").
    """
    result = _run_git(["ls-remote", "--heads", upstream_url])
    branches: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        _, ref = line.split("\t", 1)
        prefix = "refs/heads/"
        if ref.startswith(prefix):
            branches.append(ref[len(prefix):])
    return branches


def _commit_ref(sha: str) -> str:
    return f"refs/bcatlas/commits/{sha}"


def _ensure_mirror(mirror_dir: Path, upstream_url: str) -> None:
    """Ensure a shared bare mirror exists at mirror_dir, initializing it if
    not. A bare repo (no working tree) is used -- every operation here is
    `git show`/`git log`/`git fetch`, never a checkout.
    """
    if (mirror_dir / "HEAD").is_file():
        return
    mirror_dir.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "--bare", str(mirror_dir)])
    _run_git(["remote", "add", "origin", upstream_url], cwd=mirror_dir)


def _object_exists(sha: str, mirror_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=mirror_dir,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _is_ancestor(candidate_sha: str, of_sha: str, mirror_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", candidate_sha, of_sha],
        cwd=mirror_dir,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def fetch_commit(
    sha: str,
    mirror_dir: Path = DEFAULT_MIRROR_DIR,
    upstream_url: str = UPSTREAM_URL,
) -> None:
    """Scoped `git fetch origin <sha> --depth 1` into the shared mirror.

    Reuses the shared mirror across calls rather than cloning fresh per
    request. A no-op if the commit is already present locally (at any
    depth) -- see module docstring for why this check matters (a bare
    depth-1 refetch would otherwise shrink an already-deepened history).
    """
    _ensure_mirror(mirror_dir, upstream_url)
    if _object_exists(sha, mirror_dir):
        return
    _run_git(["fetch", "origin", f"{sha}:{_commit_ref(sha)}", "--depth", "1"], cwd=mirror_dir)


def read_blob(
    sha: str,
    path: str,
    mirror_dir: Path = DEFAULT_MIRROR_DIR,
    upstream_url: str = UPSTREAM_URL,
) -> bytes:
    """`git show <sha>:<path>` against the shared mirror, fetching the
    commit first if it isn't already present.
    """
    fetch_commit(sha, mirror_dir, upstream_url)
    try:
        result = subprocess.run(
            ["git", "show", f"{sha}:{path}"],
            cwd=mirror_dir,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise GitOpsError(
            f"git show {sha}:{path} failed (exit {e.returncode}): "
            f"{e.stderr.decode('utf-8', errors='replace').strip()}"
        ) from e
    return result.stdout


def _branch_ref(branch: str) -> str:
    return f"refs/bcatlas/branches/{branch}"


def list_commits(
    branch: str,
    mirror_dir: Path = DEFAULT_MIRROR_DIR,
    upstream_url: str = UPSTREAM_URL,
) -> list[tuple[str, str]]:
    """`(commit_sha, commit_message)` pairs reachable on `branch`, newest
    first (plain `git log` order -- callers needing a different order, e.g.
    resolver.py picking a max, don't care about order at all).

    Used by resolver.py to enumerate a country/major-version branch's real
    build history for version-spec resolution (exact and loose "major.minor"
    matching) -- there is no cheaper way to learn every build's exact
    version string than reading each commit's message.

    Fetches the branch with `--filter=blob:none` (a "blobless" partial
    clone) rather than a full fetch or per-commit shallow fetches: this
    pulls every commit + tree on the branch (enough to read `%H`/`%s` for
    all of them) without downloading historical file contents, which this
    function never needs. Verified directly against the real upstream repo
    this session: a full blobless fetch of `w1-28` (4075 commits) took ~25s
    and ~130MB the first time, then <1s on a repeat call against the same
    mirror (git's own fetch-negotiation short-circuits already-present
    history) -- safe to call on every resolution request, not just once.

    Lands on a persistent local ref (`refs/bcatlas/branches/<branch>`), same
    rationale as `fetch_commit`'s per-commit refs: keeps the history
    reachable/safe from GC and re-fetchable without relying on ephemeral
    `FETCH_HEAD`.
    """
    _ensure_mirror(mirror_dir, upstream_url)
    ref = _branch_ref(branch)
    _run_git(
        ["fetch", "origin", f"{branch}:{ref}", "--filter=blob:none"],
        cwd=mirror_dir,
    )
    # \x1f (unit separator) as the sha/message delimiter -- commit messages
    # here are single-line version strings (never contain it), unlike a
    # space or tab which could theoretically appear in one.
    result = _run_git(["log", "--format=%H\x1f%s", ref], cwd=mirror_dir)
    commits: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        line = line.strip("\n")
        if not line:
            continue
        sha, _, message = line.partition("\x1f")
        commits.append((sha, message))
    return commits


def commit_message(
    sha: str,
    mirror_dir: Path = DEFAULT_MIRROR_DIR,
    upstream_url: str = UPSTREAM_URL,
) -> str:
    """The exact commit message for `sha` (resolver.py's exact-commit-sha
    resolution path needs this -- the message IS the version string, per
    this repository's convention). Fetches the commit first if it isn't
    already present locally.
    """
    fetch_commit(sha, mirror_dir, upstream_url)
    result = _run_git(["log", "-1", "--format=%s", sha], cwd=mirror_dir)
    return result.stdout.strip()


def list_tree(
    sha: str,
    mirror_dir: Path = DEFAULT_MIRROR_DIR,
    upstream_url: str = UPSTREAM_URL,
) -> list[str]:
    """Every real file path in the tree at `sha` (`git ls-tree -r
    --name-only`), fetching the commit first if not already present.

    Used by diff.py's `locate_symbol_file` to find which file contains a
    named object: real on-disk AL filenames for extension objects use
    inconsistent case/abbreviations (e.g. "PageExt"/"Pageext"/"pageext" all
    occur for the same `pageextension` object_type, confirmed against the
    real w1-28 corpus), so the target path can't be constructed directly
    from `object_type`/`object_name` alone -- it must be located by
    scanning the real committed tree instead. Measured directly against the
    real ~19k-file w1-28 corpus this session at <20ms, cheap enough to
    always confirm against real content rather than guess a path.
    """
    fetch_commit(sha, mirror_dir, upstream_url)
    result = _run_git(["ls-tree", "-r", "--name-only", sha], cwd=mirror_dir)
    return [line for line in result.stdout.splitlines() if line.strip()]


def diff_paths(
    from_sha: str,
    to_sha: str,
    path: str,
    mirror_dir: Path = DEFAULT_MIRROR_DIR,
    upstream_url: str = UPSTREAM_URL,
) -> str:
    """Real `git diff <from_sha> <to_sha> -- <path>` text against the shared
    mirror, fetching both commits first if not already present. Used by
    diff.py's file-scoped diff (FR-006) -- deliberately scoped to exactly
    one path, never invoked without one, so a whole-repository diff can
    never be produced through this function (FR-007 is enforced by the
    caller, diff.py, before this is ever reached).
    """
    fetch_commit(from_sha, mirror_dir, upstream_url)
    fetch_commit(to_sha, mirror_dir, upstream_url)
    result = _run_git(["diff", from_sha, to_sha, "--", path], cwd=mirror_dir)
    return result.stdout


def log_for_path(
    path: str,
    from_sha: str,
    to_sha: str,
    mirror_dir: Path = DEFAULT_MIRROR_DIR,
    upstream_url: str = UPSTREAM_URL,
) -> list[str]:
    """Commits that touched `path` within (from_sha, to_sha], oldest-first.

    Oldest-first (unlike plain `git log`, which is newest-first) since
    callers (history.py) walk this as a change timeline from `from_version`
    toward `to_version` -- see data-model.md SymbolHistoryResult.

    `from_sha` and `to_sha` must be on a connected line of history (i.e.
    `from_sha` an ancestor of `to_sha`) within `_MAX_LOG_DEPTH` commits --
    raises ValueError, not GitOpsError, if that's not established (a caller
    input problem: shas from different branches, or given in the wrong
    order), distinct from a real git/network failure.
    """
    _ensure_mirror(mirror_dir, upstream_url)
    ref = _commit_ref(to_sha)
    depth = _INITIAL_LOG_DEPTH
    while not _is_ancestor(from_sha, to_sha, mirror_dir):
        if depth > _MAX_LOG_DEPTH:
            raise ValueError(
                f"{from_sha} is not an ancestor of {to_sha} within "
                f"{_MAX_LOG_DEPTH} commits of history -- check both shas are "
                "on the same branch and given oldest-to-newest."
            )
        _run_git(["fetch", "origin", f"{to_sha}:{ref}", "--depth", str(depth)], cwd=mirror_dir)
        depth *= 2

    result = _run_git(
        ["log", "--format=%H", f"{from_sha}..{to_sha}", "--", path],
        cwd=mirror_dir,
    )
    shas = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    shas.reverse()  # git log is newest-first; documented oldest-first return above
    return shas
