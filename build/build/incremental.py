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

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
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
_GRAPHIFY_IGNORE_TEMPLATE = Path(__file__).with_name("graphify.ignore.template")


def _ensure_graphify_ignore(search_dir: Path) -> None:
    """Write this project's `.graphifyignore` into `search_dir` before a
    graph build (see `_GRAPHIFY_IGNORE_TEMPLATE` for why -- keeps
    document/media files out of the structural AL code graph). `search_dir`
    is a staging/warm checkout, not a tracked location, so this can't live
    there permanently -- write it fresh every build instead of relying on
    it surviving a checkout/reset.
    """
    shutil.copyfile(_GRAPHIFY_IGNORE_TEMPLATE, search_dir / ".graphifyignore")

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


def _apply_diff(
    base_sha: str, target_sha: str, search_dir: Path, mirror_dir: Path
) -> tuple[int, list[str]]:
    """Overwrite only the files that really differ between `base_sha` and
    `target_sha` (from `target_sha`'s real blobs) in an already-cloned
    `search_dir`. Returns `(changed_count, changed_paths)` -- `changed_paths`
    lists every path this diff actually touched (added/modified/deleted, and
    BOTH sides of a rename -- the old path needs its stale graph nodes
    evicted, the new path needs re-extraction), reused as-is by
    `_run_graphify_update` so graphify-al's own incremental rebuild doesn't
    need to recompute the same diff a second time.
    """
    result = _run_git(["diff", "--name-status", base_sha, target_sha], mirror_dir)
    changed = 0
    changed_paths: list[str] = []
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
            changed_paths.append(_old_path)
            changed_paths.append(new_path)
        elif status.startswith("D"):
            fp = search_dir / parts[1]
            if fp.exists():
                fp.unlink()
            changed_paths.append(parts[1])
        else:  # A, M, T, C, ...
            _write_blob(target_sha, parts[1], search_dir, mirror_dir)
            changed_paths.append(parts[1])
        changed += 1
    return changed, changed_paths


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


def _bootstrap_project_settings(search_dir: Path) -> None:
    """Write the known-good AL-aware project settings (see
    `_PROJECT_SETTINGS_YAML`'s docstring above) directly, instead of
    shelling out to `ccc init` -- `ccc init` only ever writes
    cocoindex-code's generic, language-agnostic default settings, which
    would silently produce an AL-empty index. Also copies `al_chunker.py`
    into the staging search dir itself as a defensive measure -- this
    project's `chunkers: module: al_chunker:al_chunker` entry is resolved
    by the shared cocoindex-code daemon via a bare `importlib.import_module`
    (confirmed by source inspection -- no per-project `sys.path` insertion
    exists anywhere in cocoindex-code, and the copy alone doesn't fix that:
    copying a file into a directory doesn't put that directory on
    `sys.path`). This was a real, two-part production outage on the hosted
    default corpus (every `bcatlas_search` call failed, first with
    `ModuleNotFoundError: No module named 'al_chunker'`, then -- after
    fixing that half -- `... 'tree_sitter'`, al_chunker's own real
    dependency) before `_run_ccc_index` started passing `--with-editable
    <chunker dir>` to `uv run`: that builds an ephemeral overlay venv
    merging cocoindex-code's own locked deps with chunker/'s (bc-al-chunker)
    real ones, which the daemon this subprocess spawns inherits too (it
    locates its own `ccc` executable via `Path(sys.executable).parent`).
    This copy is now redundant with that fix but kept as a cheap second
    line of defense.
    """
    settings_dir = search_dir / ".cocoindex_code"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "settings.yml").write_text(_PROJECT_SETTINGS_YAML, encoding="utf-8")
    if _CHUNKER_SOURCE_FILE.is_file():
        shutil.copy2(_CHUNKER_SOURCE_FILE, search_dir / _CHUNKER_SOURCE_FILE.name)


# Watchdog tuning for `_run_ccc_index` (see its docstring for why a
# watchdog exists at all). The stall timeout must sit comfortably above
# normal quiet phases (measured live: daemon start -> first index-DB write
# took under a minute once unblocked, and steady-state writes land every
# few seconds) and well below the observed hang onsets (~13-31 min in), so
# a stall is recovered in minutes without ever tripping on a healthy run.
_CCC_POLL_INTERVAL_S = 15.0
_CCC_STALL_TIMEOUT_S = float(os.environ.get("BCATLAS_CCC_STALL_TIMEOUT_S", "300"))
_CCC_MAX_TOTAL_S = float(os.environ.get("BCATLAS_CCC_INDEX_MAX_TOTAL_S", str(4 * 3600)))
# Restarts that resume partial progress are expected and unbounded (within
# the wall-clock ceiling) -- the observed hang can recur several times in
# one build. What's NOT acceptable is restarting without ever advancing:
# that means something is genuinely broken (bad settings, dead GPU, ...)
# and retrying is just a hot loop.
_CCC_MAX_ZERO_PROGRESS_RESTARTS = 3


def _index_state_fingerprint(search_dir: Path) -> tuple[int, int]:
    """Progress signal for the `ccc index` watchdog: `(total_bytes,
    newest_mtime_ns)` across everything under `.cocoindex_code/` (the
    target SQLite DB + its WAL + the LMDB tracking state), which real
    indexing writes to every few seconds (measured live). `lock.mdb` is
    excluded because LMDB touches it merely on environment *open*, so a
    daemon restart would otherwise register as fake progress.
    """
    total = 0
    newest = 0
    for p in (search_dir / ".cocoindex_code").rglob("*"):
        if p.name == "lock.mdb":
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        total += st.st_size
        newest = max(newest, st.st_mtime_ns)
    return (total, newest)


def _kill_daemon_by_pidfile(runtime_dir: Path) -> None:
    """SIGTERM -> SIGKILL the per-build `ccc run-daemon` via its own
    `daemon.pid` file. Deliberately NOT `ccc daemon stop`: its graceful
    path does a blocking socket `recv_bytes()` against the daemon
    (confirmed by source inspection of `cocoindex_code/client.py`'s
    `stop_daemon`), which against the exact deadlocked-daemon state this
    is here to clean up could itself hang. Killing by PID cannot.
    SIGKILL'ing the process-group (the daemon is its own session leader,
    per `client.start_daemon`'s `start_new_session=True`) also reaps its
    spawned GPU-worker child, which was observed live to survive -- and
    keep ~7GB of GPU memory resident -- when only the daemon PID was
    killed.
    """
    pid_file = runtime_dir / "daemon.pid"
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return
    for sig, grace_s in ((signal.SIGTERM, 10.0), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            # Not a process-group leader after all -- fall back to the PID.
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.2)


def _run_ccc_index(search_dir: Path, init_if_needed: bool) -> None:
    """Real `ccc index` against `search_dir`, bootstrapping AL-aware
    project settings first if this is a cold build with no cloned base
    (see `_bootstrap_project_settings`). `ccc`'s own CLI has no positional
    project-root argument -- it discovers the project by walking up from
    `Path.cwd()` (confirmed by direct source inspection of
    `cocoindex_code/cli.py`'s `require_project_root`), so `cwd=search_dir`
    is what actually scopes it, not an argument.

    Two deliberate defenses around what is otherwise a plain subprocess
    call, both responses to real, live-reproduced failures -- never
    theorized (constitution Principle V), and neither fixable inside the
    vendored `cocoindex-code` package itself (Principle VI):

    1. ISOLATED per-build daemon. `COCOINDEX_CODE_RUNTIME_DIR`
       (deliberately NOT `COCOINDEX_CODE_DIR` -- that one governs
       `global_settings.yml`, the embedding-model config, which must stay
       shared/discoverable) points the daemon's socket/pid/log at a
       directory scoped to this build, so `ccc`'s client auto-start logic
       spins up a private daemon process whose `GPURunner`/`BatchQueue`
       singletons are never shared with the always-on search server's
       daemon on :8801. Beyond honoring the constitution's build/serve
       resource separation, this matters concretely: two daemons pointed
       at the SAME project directory block each other on the project's
       LMDB/SQLite state (observed live -- a freshly started daemon sat
       0%-CPU idle for 5+ minutes until a defunct older daemon holding
       those locks was killed, then proceeded within seconds).

    2. STALL WATCHDOG with kill-and-resume retry. `ccc index` has a real,
       repeatedly reproduced (4+ independent occurrences in one session)
       upstream stall: after minutes of healthy progress (GPU busy, index
       DB growing steadily) the daemon goes permanently idle -- every
       thread sleeping, zero CPU accrual across samples, GPU at background
       noise, client blocked forever on a still-ESTABLISHED unix socket
       with 0 bytes queued. Not a crash, not GPU compute in flight; it
       never recovers (30+ min observed). Onset varies (~13-31 min in, DB
       at anywhere 72-463MB), it reproduces with an isolated daemon AND
       with `COCOINDEX_RUN_GPU_IN_SUBPROCESS=1`, so it is not the shared-
       daemon contention above nor simple GPU-runner lock contention. Root
       cause lives somewhere in cocoindex's Rust/async internals and is
       out of reach (Principle VI). The workaround leans on a VERIFIED
       property (tested live, this session, by killing a run mid-flight
       and re-running: the index DB resumed growing from where it stopped
       within ~40s, no reset, no re-embedding of completed files):
       `ccc index` is resumable via its own LMDB tracking state in
       `.cocoindex_code/cocoindex.db`. So: poll the on-disk index state
       for progress, and on a genuine stall kill the client + daemon and
       simply run `ccc index` again -- each retry keeps all completed
       work. Stale `daemon.sock`/`daemon.pid` are removed after a kill
       because the client's `is_daemon_running` merely checks that the
       socket *file* exists (source-inspected), so leftovers would make
       the retry connect to nothing and fail instead of auto-starting a
       fresh daemon.

    The per-build daemon is torn down when indexing finishes (success or
    failure) so a build doesn't leave a ~GB-of-GPU-memory process resident
    forever; teardown is best-effort and never masks the real outcome.
    """
    settings_file = search_dir / ".cocoindex_code" / "settings.yml"
    if init_if_needed and not settings_file.is_file():
        _bootstrap_project_settings(search_dir)

    # The runtime dir holds a real AF_UNIX socket (`daemon.sock`) -- Linux
    # caps `sockaddr_un.sun_path` at 108 bytes, and `search_dir` (nested
    # under `data/staging/<country>-<40-char-sha>-<timestamp>-<hash>/search`)
    # is already close to that on its own, so the socket must live under a
    # short, unique path outside the repo tree rather than inside
    # `search_dir` itself (confirmed live: nesting it there raised a real
    # `OSError: AF_UNIX path too long` from the daemon on startup -- caught
    # by actually running the fix, not assumed).
    runtime_dir = Path(tempfile.gettempdir()) / "bcatlas-ccc-build" / hashlib.sha256(
        str(search_dir).encode()
    ).hexdigest()[:16]
    env = dict(os.environ)
    env["COCOINDEX_CODE_RUNTIME_DIR"] = str(runtime_dir)
    # See `scripts/start-search-server.sh` -- the embedding model is already
    # fully cached locally, but without this the daemon still does dozens
    # of etag-freshness round trips to huggingface.co on every cold start
    # before loading from cache, which on the hosted VM took over 90s and
    # tripped the search server's stall watchdog (unrelated to this
    # subprocess directly, but the same daemon code path, so the same real
    # fix applies here too).
    env["HF_HUB_OFFLINE"] = "1"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    # Client output goes to a file, not a pipe: the client streams rich
    # spinner updates continuously, and an undrained pipe would fill and
    # block it -- indistinguishable from the very stall being watched for.
    client_log = runtime_dir / "index-client.log"

    started = time.monotonic()
    zero_progress_restarts = 0
    attempt = 0
    try:
        while True:
            attempt += 1
            attempt_start_fp = _index_state_fingerprint(search_dir)
            with open(client_log, "ab") as log_fd:
                proc = subprocess.Popen(
                    [
                        "uv",
                        "run",
                        "--project",
                        str(_COCOINDEX_PROJECT),
                        "--with-editable",
                        str(_CHUNKER_SOURCE_FILE.parent),
                        "ccc",
                        "index",
                    ],
                    cwd=search_dir,
                    env=env,
                    stdout=log_fd,
                    stderr=log_fd,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            last_fp = attempt_start_fp
            last_progress = time.monotonic()
            stalled = False
            while True:
                try:
                    proc.wait(timeout=_CCC_POLL_INTERVAL_S)
                    break
                except subprocess.TimeoutExpired:
                    pass
                now = time.monotonic()
                fp = _index_state_fingerprint(search_dir)
                if fp != last_fp:
                    last_fp = fp
                    last_progress = now
                elif now - last_progress >= _CCC_STALL_TIMEOUT_S:
                    stalled = True
                    break
                if now - started >= _CCC_MAX_TOTAL_S:
                    raise IncrementalBuildError(
                        f"ccc index exceeded the {_CCC_MAX_TOTAL_S:.0f}s total wall-clock "
                        f"ceiling (attempt {attempt}); see {client_log}"
                    )

            if not stalled:
                if proc.returncode == 0:
                    return
                tail = ""
                try:
                    tail = client_log.read_text(errors="replace")[-2000:]
                except OSError:
                    pass
                raise IncrementalBuildError(
                    f"ccc index failed (exit {proc.returncode}): {tail.strip()}"
                )

            # Stalled: kill client + daemon and go around again -- resume is
            # cheap (verified, see docstring), so the only unrecoverable
            # cases are "never advances at all" and the wall-clock ceiling.
            print(
                f"ccc index on {search_dir} stalled (no on-disk progress for "
                f"{_CCC_STALL_TIMEOUT_S:.0f}s, attempt {attempt}); killing and resuming",
                file=sys.stderr,
                flush=True,
            )
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait()
            _kill_daemon_by_pidfile(runtime_dir)
            (runtime_dir / "daemon.sock").unlink(missing_ok=True)
            (runtime_dir / "daemon.pid").unlink(missing_ok=True)

            if _index_state_fingerprint(search_dir) == attempt_start_fp:
                zero_progress_restarts += 1
                if zero_progress_restarts >= _CCC_MAX_ZERO_PROGRESS_RESTARTS:
                    raise IncrementalBuildError(
                        f"ccc index stalled {zero_progress_restarts} consecutive times "
                        f"without any progress (attempt {attempt}); see {client_log} "
                        f"and {runtime_dir / 'daemon.log'}"
                    )
            else:
                zero_progress_restarts = 0
    finally:
        _kill_daemon_by_pidfile(runtime_dir)
        shutil.rmtree(runtime_dir, ignore_errors=True)


def _run_graphify_update(
    search_dir: Path, graph_dir: Path, changed_paths: list[str] | None = None
) -> None:
    """Real `python -m graphify update <search_dir>` with `GRAPHIFY_OUT`
    pointed at `graph_dir` (an absolute path -- `graphify` joins it with
    `watch_path` via `Path.__truediv__`, which for an absolute right-hand
    side simply returns the absolute path unchanged, so this lands
    `graph.json` in the layout-defined staging graph dir instead of nested
    under the search dir, matching `layout.py`'s split search/graph
    convention).

    `changed_paths` (from `_apply_diff`'s real `git diff --name-status`
    output -- `None` for a cold build with no base to diff against) is
    forwarded via `graphify update`'s `--changed-paths-file` flag, an
    addition this project made to the `graphify-al` fork specifically for
    this: `graphify-al` is OUR maintained fork (constitution Principle VI
    only protects the vendored, unforked `cocoindex-code`), and it already
    had the underlying incremental-rebuild machinery
    (`graphify.watch._rebuild_code`'s `changed_paths` parameter, previously
    only reachable from its own git-hook integration) -- it just wasn't
    exposed on the `update` CLI subcommand our build pipeline shells out to.
    Measured live (this session): a real minor-version hop where only 1231
    of 19276 files actually changed took graphify ~561s with a full
    re-extraction, ~433s once wired to extract only the real diff (a real
    but MODEST win, not proportional to the 1231/19276 file ratio) --
    `_rebuild_code` skips AST extraction for unchanged files and preserves
    their nodes/edges instead of re-deriving them, but still re-runs full-
    graph community clustering, labeling, report generation, and JSON
    export over the ENTIRE merged graph (265K+ nodes) every time, none of
    which is incremental. Those full-graph steps -- not AST extraction --
    dominate this pipeline's wall-clock, which is why the speedup is real
    but far smaller than the changed-file ratio would suggest.

    Always passes `--no-report` (bc-code-atlas's own addition to the
    graphify-al fork, same rationale as `--changed-paths-file` above): a
    later profiling pass (cProfile on a real 75-changed-file incremental
    build) found `suggest_questions`'s full-graph `betweenness_centrality`
    call alone -- purely to populate a GRAPH_REPORT.md section -- at ~30%
    of `graphify update`'s total wall-clock on this corpus's 267K-node
    graph, and none of it (score_all/god_nodes/surprising_connections/
    suggest_questions/generate) feeds graph.json or any served MCP tool;
    `bcatlas_god_nodes` and the `graphify://surprises` resource recompute
    their own outputs live from the graph on query instead of reading this
    build-time output (see graphify-al's serve.py). GRAPH_REPORT.md and
    `.graphify_labels.json` are simply left stale/untouched in the staging
    (and therefore promoted warm) dir rather than regenerated on every
    build -- nothing in this project's own build/serve stack reads either
    file. IMPORTANT caller obligation established by the earlier measurement:
    `changed_paths`
    only helps if the STAGING `graph_dir` already contains the base's prior
    `graph.json` before this call -- `_rebuild_code` reads `existing
    graph.json` from wherever `GRAPHIFY_OUT` (`graph_dir`) points, and an
    empty staging graph dir (this build's own, not the base's) silently
    produces a corpus-collapsed graph (measured live: 45,610 nodes instead
    of the expected ~265K) with NO error -- see `build_version`'s
    `base_warm_graph_dir` clone, which must run before this. Omitting
    `changed_paths` (a cold build, or any caller that doesn't pass it) is
    unaffected -- `graphify update`'s own default remains the original full
    re-extraction.
    """
    graph_dir.mkdir(parents=True, exist_ok=True)
    _ensure_graphify_ignore(search_dir)
    env = dict(os.environ)
    env["GRAPHIFY_OUT"] = str(graph_dir)
    cmd = [
        "uv", "run", "--project", str(_GRAPHIFY_PROJECT),
        "python", "-m", "graphify", "update", str(search_dir),
        "--no-report",
    ]

    changed_paths_file: Path | None = None
    try:
        if changed_paths is not None:
            fd, tmp_name = tempfile.mkstemp(prefix="bcatlas-graphify-changed-", suffix=".txt")
            changed_paths_file = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(changed_paths))
            cmd += ["--changed-paths-file", str(changed_paths_file)]

        result = subprocess.run(cmd, cwd=_GRAPHIFY_PROJECT, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise IncrementalBuildError(
                f"graphify update failed (exit {result.returncode}): {result.stderr.strip()}"
            )
    finally:
        if changed_paths_file is not None:
            changed_paths_file.unlink(missing_ok=True)


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
    changed_paths: list[str] | None = None

    if base is not None:
        shutil.copytree(base.warm_search_dir, search_dir)
        # graphify-al's incremental rebuild (`--changed-paths-file`, see
        # `_run_graphify_update`) only preserves nodes/edges for unchanged
        # files by reading whatever `graph.json` already sits in the graph
        # dir it's pointed at (`GRAPHIFY_OUT`) -- that's THIS build's fresh
        # staging graph dir, not the base's warm one, unless we seed it.
        # Skipping this clone was tried first and measured live: it silently
        # produced a graph with only the ~1231 changed files' worth of nodes
        # (45,610) instead of preserving the base's other ~18,000 unchanged
        # files (263,921 total) -- `_rebuild_code` has no way to know they
        # ever existed if `existing_graph.json` isn't there to read.
        base_warm_graph_dir = layout.warm_graph_dir(base.country, base.commit_sha, data_dir)
        if base_warm_graph_dir.is_dir():
            shutil.copytree(base_warm_graph_dir, graph_dir)
        changed_file_count, changed_paths = _apply_diff(base.commit_sha, commit_sha, search_dir, mirror_dir)
    else:
        _extract_tree(commit_sha, search_dir, mirror_dir)

    _run_ccc_index(search_dir, init_if_needed=base is None)
    _run_graphify_update(search_dir, graph_dir, changed_paths=changed_paths)

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
