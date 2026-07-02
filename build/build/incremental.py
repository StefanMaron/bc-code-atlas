"""Clone-nearest-warm-sibling incremental builds (T024; FR-014).

Given a target `(country, commit_sha)` to build, this module:

1. Picks a base (`select_base`): the nearest already-warm sibling of the
   SAME country by build-number proximity if one exists; else any other
   warm `(country, version)` with high REAL content overlap (measured via
   `git diff --name-only` file counts between the two commits, never git
   commit-ancestry -- constitution Principle V, research.md); else `None`
   (cold build).
2. If a base was found: clones its warm search directory (source tree +
   its `.cocoindex_code/` state -- this is what makes cocoindex-code's own
   stock incremental change detection actually kick in, per research.md's
   "incremental builds" decision) into the new staging search directory,
   then overwrites ONLY the files that really differ between the base and
   target commits, from the target commit's real blobs.
3. If no base: fetches the target commit's full tree fresh via `git
   archive` (equivalent to a real checkout, without needing a working tree
   in the shared bare mirror).
4. Either way, runs cocoindex-code's stock `ccc index` CLI (as a real
   subprocess -- this project deliberately never imports cocoindex-code
   in-process, see `pyproject.toml`) against the staging search dir, then
   `graphify-al`'s `python -m graphify update` against the same tree,
   writing `graph.json` into the staging graph dir (no incremental graph
   mode this cut -- graph extraction has no ML cost, per plan.md).

This module is synchronous/blocking end to end (real subprocesses, real
file I/O) -- `queue.py`/`mcp_server.py` run it via `asyncio.to_thread`, it
never touches `asyncio` itself.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from registry import git_ops
from registry.resolver import parse_version_string

from . import eviction, layout

# build/build/incremental.py -> parents[2] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_COCOINDEX_PROJECT = _REPO_ROOT / "tools" / "cocoindex-code"
_GRAPHIFY_PROJECT = _REPO_ROOT / "tools" / "graphify-al"

# `chunker/`'s own AL-aware chunker plugin (T028's domain, NOT edited here --
# read-only reuse). A brand-new `ccc init` writes cocoindex-code's GENERIC
# default project settings, which has no `.al` in `include_patterns` and no
# `chunkers` entry at all -- confirmed live this session: a fresh `ccc init`
# against a staging dir produced a settings.yml with zero AL-related config,
# which would silently index 0 real files. The one known-good schema is the
# hand-authored `data/.cocoindex_code/settings.yml` already used by today's
# single-version setup (`chunkers: [{ext: al, module: al_chunker:al_chunker}]`)
# -- a cold build must bootstrap the SAME schema, not cocoindex-code's
# generic default, or the resulting index would be structurally empty of AL
# content while still reporting a "successful" `ccc index` run.
_CHUNKER_SOURCE_FILE = _REPO_ROOT / "chunker" / "al_chunker.py"

_PROJECT_SETTINGS_YAML = """\
include_patterns:
  - "**/*.al"
  - "**/*.md"
exclude_patterns:
  - "**/.git/**"
  - "**/graphify-out/**"
language_overrides:
  - ext: al
    lang: al
chunkers:
  - ext: al
    module: al_chunker:al_chunker
"""

# "High real content overlap" threshold for the cross-country/non-adjacent
# fallback base (FR-014's "significantly overlapping" per data-model.md's
# Assumptions section) -- research.md measured ~85-90% overlap between two
# unrelated-ancestry country branches live, so 50% is a deliberately
# conservative floor: worth reusing well below the measured typical case,
# but not so low that a barely-related pair gets cloned. Configuration, not
# an architectural constant.
MIN_OVERLAP_RATIO = float(os.environ.get("BCATLAS_BUILD_MIN_OVERLAP_RATIO", "0.5"))


class IncrementalBuildError(Exception):
    """Raised on a genuine build-step failure (git plumbing, `ccc index`,
    or `graphify update` subprocess exiting non-zero). Never raised for "no
    base found" -- that's the normal, expected cold-build path.
    """


@dataclass(frozen=True)
class BaseSelection:
    country: str
    commit_sha: str
    version_string: str
    warm_search_dir: Path
    kind: str  # "same_country" | "content_overlap"
    overlap_ratio: float | None = None


@dataclass(frozen=True)
class BuildOutcome:
    build_id: str
    country: str
    commit_sha: str
    version_string: str
    staging_search_dir: Path
    staging_graph_dir: Path
    base: BaseSelection | None
    elapsed_seconds: float
    changed_file_count: int | None  # only meaningful when base is not None


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise IncrementalBuildError(
            f"git {' '.join(args)} failed (exit {e.returncode}): {e.stderr.strip()}"
        ) from e


def _commit_message(sha: str, mirror_dir: Path) -> str:
    git_ops.fetch_commit(sha, mirror_dir=mirror_dir)
    return _run_git(["log", "-1", "--format=%s", sha], mirror_dir).stdout.strip()


def _diff_file_counts(base_sha: str, target_sha: str, mirror_dir: Path) -> tuple[int, int]:
    """Real `(changed_file_count, target_total_file_count)` between two
    commits, via `git diff --name-only`/`git ls-tree -r` -- a direct
    content comparison, never inferred from branch/commit ancestry
    (constitution Principle V: the proxy that WAS tried this session,
    GitHub's ahead_by/behind_by, was directly disproved).
    """
    git_ops.fetch_commit(base_sha, mirror_dir=mirror_dir)
    git_ops.fetch_commit(target_sha, mirror_dir=mirror_dir)
    changed = _run_git(["diff", "--name-only", base_sha, target_sha], mirror_dir).stdout.splitlines()
    total = _run_git(["ls-tree", "-r", "--name-only", target_sha], mirror_dir).stdout.splitlines()
    return len(changed), len(total)


def select_base(
    country: str,
    target_commit_sha: str,
    target_version_string: str,
    data_dir: Path = layout.DEFAULT_DATA_DIR,
    mirror_dir: Path = git_ops.DEFAULT_MIRROR_DIR,
) -> BaseSelection | None:
    """Nearest already-warm sibling to clone-and-patch from, or `None` for
    a cold build. See module docstring for the two-tier selection rule.
    """
    entries = [
        e
        for e in eviction.scan_warm_entries(data_dir)
        if not (e.country == country and e.version == target_commit_sha)
    ]
    if not entries:
        return None

    target_parsed = parse_version_string(target_version_string)

    # Tier 1: same country, nearest by build-number proximity.
    if target_parsed is not None:
        scored: list[tuple[int, "eviction.WarmEntry", str]] = []
        for entry in entries:
            if entry.country != country:
                continue
            version_string = _commit_message(entry.version, mirror_dir)
            parsed = parse_version_string(version_string)
            if parsed is None:
                continue
            distance = abs(parsed.build_b - target_parsed.build_b)
            scored.append((distance, entry, version_string))
        if scored:
            scored.sort(key=lambda t: t[0])
            _distance, entry, version_string = scored[0]
            return BaseSelection(
                country=entry.country,
                commit_sha=entry.version,
                version_string=version_string,
                warm_search_dir=layout.warm_search_dir(entry.country, entry.version, data_dir),
                kind="same_country",
            )

    # Tier 2: any warm (country, version) with high real content overlap.
    best: tuple[float, "eviction.WarmEntry"] | None = None
    for entry in entries:
        changed, total = _diff_file_counts(entry.version, target_commit_sha, mirror_dir)
        overlap = 1.0 - (changed / total) if total else 0.0
        if overlap >= MIN_OVERLAP_RATIO and (best is None or overlap > best[0]):
            best = (overlap, entry)
    if best is not None:
        overlap, entry = best
        version_string = _commit_message(entry.version, mirror_dir)
        return BaseSelection(
            country=entry.country,
            commit_sha=entry.version,
            version_string=version_string,
            warm_search_dir=layout.warm_search_dir(entry.country, entry.version, data_dir),
            kind="content_overlap",
            overlap_ratio=overlap,
        )

    return None


def _write_blob(sha: str, path: str, dest_root: Path, mirror_dir: Path) -> None:
    content = git_ops.read_blob(sha, path, mirror_dir=mirror_dir)
    dest = dest_root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)


def _apply_diff(base_sha: str, target_sha: str, search_dir: Path, mirror_dir: Path) -> int:
    """Overwrite only the files that really differ between `base_sha` and
    `target_sha` (from `target_sha`'s real blobs) in an already-cloned
    `search_dir`. Returns the number of changed paths actually applied.
    """
    result = _run_git(["diff", "--name-status", base_sha, target_sha], mirror_dir)
    changed = 0
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R"):  # rename: status, old_path, new_path
            _old_path, new_path = parts[1], parts[2]
            old_fp = search_dir / _old_path
            if old_fp.exists():
                old_fp.unlink()
            _write_blob(target_sha, new_path, search_dir, mirror_dir)
        elif status.startswith("D"):
            fp = search_dir / parts[1]
            if fp.exists():
                fp.unlink()
        else:  # A, M, T, C, ...
            _write_blob(target_sha, parts[1], search_dir, mirror_dir)
        changed += 1
    return changed


def _extract_tree(sha: str, dest_dir: Path, mirror_dir: Path) -> None:
    """Fresh full-tree extraction of `sha` into `dest_dir`, for a cold
    build with no usable base. `git archive` works directly against the
    bare mirror (no working tree needed) as long as the commit's blobs are
    locally present -- guaranteed here since `fetch_commit`'s depth-1 fetch
    pulls the full tree+blobs for that one commit (unlike
    `git_ops.list_commits`'s blobless branch-history fetch).
    """
    git_ops.fetch_commit(sha, mirror_dir=mirror_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive = subprocess.Popen(
        ["git", "archive", "--format=tar", sha], cwd=mirror_dir, stdout=subprocess.PIPE
    )
    assert archive.stdout is not None
    try:
        extract = subprocess.run(["tar", "-x", "-C", str(dest_dir)], stdin=archive.stdout)
    finally:
        archive.stdout.close()
        archive.wait()
    if archive.returncode != 0:
        raise IncrementalBuildError(f"git archive {sha} failed (exit {archive.returncode})")
    if extract.returncode != 0:
        raise IncrementalBuildError(f"tar extract of {sha} into {dest_dir} failed (exit {extract.returncode})")


def _run_uv(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(["uv", *args], cwd=cwd, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise IncrementalBuildError(
            f"uv {' '.join(args)} (cwd={cwd}) failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result


def _bootstrap_project_settings(search_dir: Path) -> None:
    """Write the known-good AL-aware project settings (see
    `_PROJECT_SETTINGS_YAML`'s docstring above) directly, instead of
    shelling out to `ccc init` -- `ccc init` only ever writes
    cocoindex-code's generic, language-agnostic default settings, which
    would silently produce an AL-empty index. Also copies `al_chunker.py`
    into the staging search dir itself as a defensive measure: this
    project's `chunkers: module: al_chunker:al_chunker` entry is resolved
    by the shared cocoindex-code daemon via a bare `importlib.import_module`
    (confirmed by source inspection -- no per-project `sys.path` insertion
    exists anywhere in cocoindex-code), so whether it actually resolves
    depends on that daemon process's own `sys.path` at daemon-start time,
    not on anything this subprocess call controls. See
    `build/build/mcp_server.py`'s module docstring / this session's final
    report for the full finding -- this is a real, load-bearing integration
    risk for T028's multi-tenant chunker refactor, not fully solvable from
    here.
    """
    settings_dir = search_dir / ".cocoindex_code"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "settings.yml").write_text(_PROJECT_SETTINGS_YAML, encoding="utf-8")
    if _CHUNKER_SOURCE_FILE.is_file():
        shutil.copy2(_CHUNKER_SOURCE_FILE, search_dir / _CHUNKER_SOURCE_FILE.name)


def _run_ccc_index(search_dir: Path, init_if_needed: bool) -> None:
    """Real `ccc index` against `search_dir`, bootstrapping AL-aware
    project settings first if this is a cold build with no cloned base
    (see `_bootstrap_project_settings`). `ccc`'s own CLI has no positional
    project-root argument -- it discovers the project by walking up from
    `Path.cwd()` (confirmed by direct source inspection of
    `cocoindex_code/cli.py`'s `require_project_root`), so `cwd=search_dir`
    is what actually scopes it, not an argument.
    """
    settings_file = search_dir / ".cocoindex_code" / "settings.yml"
    if init_if_needed and not settings_file.is_file():
        _bootstrap_project_settings(search_dir)
    _run_uv(["run", "--project", str(_COCOINDEX_PROJECT), "ccc", "index"], cwd=search_dir)


def _run_graphify_update(search_dir: Path, graph_dir: Path) -> None:
    """Real `python -m graphify update <search_dir>` with `GRAPHIFY_OUT`
    pointed at `graph_dir` (an absolute path -- `graphify` joins it with
    `watch_path` via `Path.__truediv__`, which for an absolute right-hand
    side simply returns the absolute path unchanged, so this lands
    `graph.json` in the layout-defined staging graph dir instead of nested
    under the search dir, matching `layout.py`'s split search/graph
    convention). No incremental graph mode this cut (plan.md) -- always a
    full re-extraction, which has no ML cost.
    """
    graph_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["GRAPHIFY_OUT"] = str(graph_dir)
    result = subprocess.run(
        ["uv", "run", "--project", str(_GRAPHIFY_PROJECT), "python", "-m", "graphify", "update", str(search_dir)],
        cwd=_GRAPHIFY_PROJECT,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise IncrementalBuildError(f"graphify update failed (exit {result.returncode}): {result.stderr.strip()}")


def build_version(
    country: str,
    commit_sha: str,
    version_string: str,
    build_id: str,
    data_dir: Path = layout.DEFAULT_DATA_DIR,
    mirror_dir: Path = git_ops.DEFAULT_MIRROR_DIR,
) -> BuildOutcome:
    """Produce a complete staging build at `layout.staging_root(build_id)`
    for `(country, commit_sha)`. Blocking end-to-end; run via
    `asyncio.to_thread` from an event loop. Does NOT promote to warm --
    that's `promote.promote(build_id, country, commit_sha, data_dir)`, a
    separate caller-driven step.
    """
    t0 = time.monotonic()
    search_dir = layout.staging_search_dir(build_id, data_dir)
    graph_dir = layout.staging_graph_dir(build_id, data_dir)
    search_dir.parent.mkdir(parents=True, exist_ok=True)

    base = select_base(country, commit_sha, version_string, data_dir, mirror_dir)
    changed_file_count: int | None = None

    if base is not None:
        shutil.copytree(base.warm_search_dir, search_dir)
        changed_file_count = _apply_diff(base.commit_sha, commit_sha, search_dir, mirror_dir)
    else:
        _extract_tree(commit_sha, search_dir, mirror_dir)

    _run_ccc_index(search_dir, init_if_needed=base is None)
    _run_graphify_update(search_dir, graph_dir)

    return BuildOutcome(
        build_id=build_id,
        country=country,
        commit_sha=commit_sha,
        version_string=version_string,
        staging_search_dir=search_dir,
        staging_graph_dir=graph_dir,
        base=base,
        elapsed_seconds=time.monotonic() - t0,
        changed_file_count=changed_file_count,
    )
