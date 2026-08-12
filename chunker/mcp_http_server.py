"""Thin HTTP wrapper around cocoindex-code's MCP server.

cocoindex-code's own `ccc mcp` CLI command hard-codes stdio transport
(cli.py calls `mcp_server.run_stdio_async()`, no --host/--port/--transport
flag exists). This needs the MCP server reachable over HTTP from a separate
Claude Code session, exactly like a remote deployment would be, so this
script builds a `bcatlas_search` tool on the same daemon client functions
`create_mcp_server()` uses and serves it over Streamable HTTP instead.
Tool names are prefixed with `bcatlas_` -- plain names like `search`
collide with IDE-builtin tools (e.g. VS Code's own search) in some MCP
clients.

It also adds one behavior on top of stock cocoindex-code: BC's own AL source
tree gives ~40% of all AL chunks to Tests-*/`*Test*`/`*Test Library*`
directories (293 files just in `Tests-ERM`, verified against the live
index). Test codeunits are written with deliberately readable names and
comments ("ApplyCustomerLedgerEntry", "// Verify: ...") that describe the
same concepts as the real implementation in natural language, so they
routinely outrank the actual `Base Application` code for concept queries
even though the embedding model itself is discriminating correctly (see
REPORT.md finding #6). The classification below is a structural fact about
Microsoft's own directory layout, not a curated business-concept mapping --
it generalizes to any query, not just the ones tested by hand.

Usage:
    uv run --project /path/to/cocoindex-code python mcp_http_server.py \
        <project_root> [--host HOST] [--port PORT]

`project_root` may point at any local directory of AL source, not only the
default Microsoft BC corpus -- see specs/005-local-source-directory/ (issue
#18). `scripts/start-search-server.sh` reads this from the optional
BCATLAS_SOURCE_DIR env var. A directory indexed for the first time needs an
AL-aware `.cocoindex_code/settings.yml` (run `ccc init`, then copy
`chunker/templates/al-source-settings.yml` into it) or `.al` files won't get
AL-specific chunking -- see that template file and
specs/005-local-source-directory/quickstart.md for the full one-time setup.

The MCP `instructions` text and search path-filter prefixes are also
overridable per directory via `<project_root>/.bcatlas/mcp_presentation.yml`
-- see specs/006-configurable-mcp-instructions/contracts/settings-file.md.
Useful together with the above: a custom AL directory can get instructions
that accurately describe it instead of the default Business Central text.

`--watch-interval-seconds` (opt-in, off by default) reindexes project_root
every N seconds in the background, so file changes are searchable without
an explicit refresh -- see
specs/007-file-watcher-reindex/contracts/watch-mode.md.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import yaml
from cocoindex_code.server import CodeChunkResult, SearchResultModel
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# Domain-specific instructions -- NOT cocoindex_code's own generic
# instructions (which say nothing about what corpus is actually indexed).
# An agent connecting to this server has zero prior context that it's
# looking at Business Central at all unless this says so explicitly.
_MCP_INSTRUCTIONS = (
    "Semantic search over Microsoft Dynamics 365 Business Central's AL"
    " base-application source and official developer documentation --"
    " for dependency and implementation investigation before writing or"
    " reviewing AL customizations."
    "\n\n"
    "Indexed corpus: the w1-28 base application source (extracted from"
    " Microsoft's own build, not decompiled), the public functional/admin"
    " BC docs, and the public AL developer/compiler reference (diagnostics,"
    " properties, methods), all in one combined index."
    "\n\n"
    "Use this to find how BC itself implements something (e.g. posting"
    " logic, a table/page/codeunit you're extending, an API pattern),"
    " locate real call-site examples, or check official docs -- before"
    " guessing at AL syntax or event names from training data alone."
    " Finds relevant code by meaning, unlike grep or text matching, even"
    " when exact keywords are unknown."
    "\n\n"
    "For the exact structural relationship graph (what calls/subscribes"
    " to a given object, extension targets, shortest path between two"
    " concepts) see the companion graph MCP server instead."
)

# Matches a whole path segment like "Tests-ERM", "System Application Test",
# "Test Library", "Test Runner" -- verified against every top-level test-ish
# directory name in the real w1-28 corpus, with zero false positives found.
_TEST_PATH_SEGMENT = re.compile(r"(?<![A-Za-z])Tests?(?![A-Za-z])")

# How much to overfetch per round when filtering out test chunks, and how
# many rounds to try before giving up and returning what we have.
_OVERFETCH_MULTIPLIER = 4
_MAX_OVERFETCH_ROUNDS = 3
_MAX_FETCH_LIMIT = 100


def _is_test_path(file_path: str) -> bool:
    return any(_TEST_PATH_SEGMENT.search(seg) for seg in file_path.split("/")[:-1])


# --- daemon stall recovery ---------------------------------------------
#
# `build/build/incremental.py`'s `_run_ccc_index` already documents (from
# 4+ independently reproduced occurrences, verified live, never theorized
# -- constitution Principle V) a real upstream `ccc index` stall: after
# minutes of healthy progress the daemon goes permanently idle (or, also
# observed live against THIS server's own shared daemon, spins CPU with
# zero GPU utilization) and never recovers on its own -- root cause is in
# cocoindex's Rust/async internals, out of reach here (Principle VI). The
# build side already has a kill-and-resume watchdog for it; this server's
# always-on shared daemon (serving `bcatlas_search` for every tester) had
# none, so a stall here just hangs every search forever with no recovery.
# This mirrors that same proven workaround at this call site instead of
# patching the vendored `cocoindex_code` package (same Principle VI
# rationale as the build side), duplicated rather than cross-imported
# because this project deliberately has no dependency on `build/` (see the
# module docstring's multi-tenant routing note above).
#
# A second, distinct cause of what looked like the same "stall" was found
# and fixed live (this session): a fresh daemon's first index of this
# corpus is a genuine ~30+ minute full reprocess (see `_progress_signal`'s
# docstring), during which the on-disk fingerprint this watchdog used to
# check stayed completely flat -- so the watchdog was killing a perfectly
# healthy, progressing reindex every `_STALL_TIMEOUT_S`, forever, before it
# could ever finish. `_progress_signal` now also polls the daemon's live
# IndexingProgress counters so this case reads as forward progress instead.
#
# A third cause, found live on the hosted VM (this session, two days after
# the above): even with the combined signal, 90s was still too short for
# THIS corpus's cold-start latency on the VM's 4-vCPU hardware -- a real
# attempt accumulated 5+ minutes of genuine CPU time (verified via `ps`
# %CPU/cumulative TIME, not assumed) with zero disk/counter movement the
# entire time before hitting the old 90s ceiling and being killed, so every
# retry restarted from scratch with nothing ever surviving long enough to
# get durably written -- an infinite loop that could never converge, not
# just a slow one. `build/build/incremental.py`'s own equivalent watchdog
# had already been tuned to 300s based on live measurement ("daemon start
# -> first index-DB write took under a minute once unblocked") -- this
# side never got the same update when it was duplicated from that one (see
# comment above). Matching that already-validated value here instead of
# re-deriving it.
_STALL_TIMEOUT_S = 300.0
_STALL_POLL_INTERVAL_S = 5.0
_MAX_STALL_RETRIES = 3

_T = TypeVar("_T")


def _index_state_fingerprint(project_root: str) -> tuple[int, float]:
    """(total_bytes, newest_mtime) across a project's on-disk cocoindex
    state -- real forward progress independent of the daemon's own
    in-memory progress counters, which can plateau mid-batch for a while
    even when healthy (observed live: flat counters for 10s+ with the
    daemon still genuinely busy). `lock.mdb`'s mtime updates on every
    read, not just writes, so it's excluded exactly as
    `build/build/incremental.py`'s identically-named helper does.
    """
    total = 0
    newest = 0.0
    root = Path(project_root) / ".cocoindex_code"
    if not root.is_dir():
        return (0, 0.0)
    for p in root.rglob("*"):
        if not p.is_file() or p.name == "lock.mdb":
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        total += st.st_size
        newest = max(newest, st.st_mtime)
    return (total, newest)


def _daemon_cpu_ticks() -> int | None:
    """Total (utime+stime) CPU jiffies for the shared `ccc` daemon process,
    read directly from `/proc/<pid>/stat` (Linux-only, matches this
    project's only deployment target -- no new dependency needed). `None`
    if the pidfile/proc entry isn't readable (daemon not up, or a
    permission/race hiccup -- best-effort, same rationale as the
    `project_status()` fallback below).

    Added after a live-reproduced false stall (this session, the hosted
    VM): `project_status()` and the on-disk fingerprint both read as flat
    for three consecutive 300s windows while the daemon (confirmed via
    direct `ps`) was continuously burning ~99%+ CPU and the target DB was
    genuinely, if slowly, growing -- `_run_with_stall_recovery` gave up and
    reported failure even though the daemon was healthy and kept running
    to real completion afterward on its own. Raw CPU ticks are a much
    cheaper and more direct "is this process actually doing something"
    signal than either of the above, and specifically distinguish this
    case from the documented genuine stall (daemon.py's real upstream bug,
    "every thread sleeping, zero CPU accrual") this watchdog exists to
    catch -- a daemon burning CPU is by definition not that.
    """
    try:
        from cocoindex_code._daemon_paths import daemon_pid_path

        pid = int(daemon_pid_path().read_text().strip())
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()
        # After the last ")" (closes the process name, which itself can
        # contain spaces/parens): field 0 is state, fields 11/12 (0-indexed
        # from there) are utime/stime in clock ticks (man proc(5)).
        return int(fields[11]) + int(fields[12])
    except (OSError, ValueError, IndexError):
        return None


def _progress_signal(project_root: str) -> tuple[int, float, tuple[int, ...], int | None]:
    """Extends `_index_state_fingerprint` with the daemon's own live
    `IndexingProgress` counters, fetched via a fresh `project_status()`
    round trip -- confirmed live (this session) that a fresh daemon's
    *first* index of this corpus is a genuine, unavoidable full reprocess
    that runs 30+ minutes with the on-disk fingerprint completely flat the
    whole time (`chunker/chunking.py`'s `CHUNKER_REGISTRY` docstring: custom
    chunker callables aren't fingerprint-able, so cocoindex-code cannot
    memoize across a daemon restart and reprocesses every file from
    scratch -- confirmed by tracing `cocoindex/_internal/memo_fingerprint.py`
    -- upstream-by-design, Principle VI, not something to patch). Disk
    writes apparently only flush in an infrequent/late batch during that
    pass, so the disk-only signal alone reads a genuinely healthy,
    progressing reindex as a stall and kills the daemon before it can ever
    finish -- an unrecoverable restart loop. `project_status()` opens its
    own independent connection (`client._send` -> `_connect_and_handshake`
    every call) and `daemon.py` dispatches each connection as its own
    asyncio task with a lock-free `get_status()` read, so it stays
    responsive even while an `index()`/`search()` call is in flight on a
    different connection -- verified by reading `daemon.py`'s
    `ProjectStatusRequest` handling. Best-effort: if the daemon is busy
    enough that even this fails or times out oddly, fall back to the disk
    signal alone rather than raising out of a progress check.

    Also includes `_daemon_cpu_ticks()` -- see its own docstring for why:
    both signals above were observed live to plateau for 300s+ at a time
    on a genuinely healthy, actively-computing daemon.
    """
    disk_total, disk_mtime = _index_state_fingerprint(project_root)
    cpu_ticks = _daemon_cpu_ticks()
    counters: tuple[int, ...] = ()
    try:
        from cocoindex_code import client as _client

        status = _client.project_status(project_root)
        if status.progress is not None:
            p = status.progress
            counters = (
                p.num_execution_starts,
                p.num_unchanged,
                p.num_adds,
                p.num_deletes,
                p.num_reprocesses,
                p.num_errors,
            )
    except Exception:
        pass
    return (disk_total, disk_mtime, counters, cpu_ticks)


def _child_daemon_pids() -> list[int]:
    """Every direct child of this server's own process whose cmdline looks
    like a `ccc run-daemon` -- a cross-check against `daemon_pid_path()`,
    not a replacement for it.

    Found live (this session, the hosted VM): the pidfile pointed at PID
    313140, already dead/zombied, while the real, actively-computing
    daemon was PID 313141 -- a completely separate process group (own
    PGID/SID, per `start_new_session=True`), spawned as a SIBLING of
    313140 (both direct children of this same server process) rather than
    ever replacing it in the pidfile. `_kill_shared_daemon_hard`'s
    `os.killpg(pidfile_pid, ...)` had been silently killing nothing but
    that already-dead zombie on every "stall" for hours, while the real
    daemon ran on completely untouched. Enumerating our own children
    directly via `/proc` (Linux-only, matches this project's only
    deployment target) catches this regardless of which PID the vendored
    client library's pidfile happens to be tracking.
    """
    own_pid = os.getpid()
    pids: list[int] = []
    try:
        proc = Path("/proc")
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                stat_fields = (entry / "stat").read_text().rsplit(")", 1)[-1].split()
                ppid = int(stat_fields[1])
                if ppid != own_pid:
                    continue
                cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ")
                if b"run-daemon" in cmdline or b"ccc" in cmdline:
                    pids.append(pid)
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        pass
    return pids


def _kill_pid_group(pid: int) -> None:
    """SIGTERM -> SIGKILL a process group by PID, tolerating either side
    already being gone. Shared by `_kill_shared_daemon_hard` for both the
    pidfile-tracked PID and any extra orphans `_child_daemon_pids` finds.
    """
    for sig, grace_s in ((signal.SIGTERM, 10.0), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
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


def _kill_shared_daemon_hard() -> None:
    """SIGTERM -> SIGKILL the shared `ccc` daemon by its own pidfile, plus
    any of this server's own child `ccc run-daemon` processes the pidfile
    doesn't mention (see `_child_daemon_pids`'s docstring for why that
    cross-check exists -- a live-reproduced stale-pidfile orphan, not a
    theoretical one).

    Deliberately NOT `cocoindex_code.client.stop_daemon()`: its graceful
    path does a blocking socket `recv_bytes()` against the daemon, which
    against the exact stalled-daemon state this exists to clean up could
    itself hang (same reasoning, and same conclusion reached independently
    by `build/build/incremental.py`'s `_kill_daemon_by_pidfile`). Killing
    by PID cannot hang. `os.killpg` also reaps the daemon's spawned
    GPU-worker child (the daemon is its own session leader --
    `client.start_daemon`'s `start_new_session=True`).

    Also resets the client module's sticky `_daemon_ensured` flag: once
    that module has successfully connected once in this server's
    lifetime, its own auto-start logic treats a vanished daemon as a fatal
    anomaly to surface rather than something to silently restart (see
    `cocoindex_code.client._connect_and_handshake`) -- without this reset,
    the very next call after killing the daemon would just fail instead of
    starting a fresh one.
    """
    from cocoindex_code import client as _daemon_client
    from cocoindex_code._daemon_paths import daemon_pid_path

    pid_file = daemon_pid_path()
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        pid = None

    targets = set(_child_daemon_pids())
    if pid is not None:
        targets.add(pid)
    for target in targets:
        _kill_pid_group(target)
    _daemon_client._daemon_ensured = False


def _run_with_stall_recovery(fn: Callable[[], _T], project_root: str) -> _T:
    """Run a blocking `cocoindex_code.client` call (`index`/`search`) to
    completion, recovering from the documented daemon stall above instead
    of hanging forever.

    `fn` runs on a background thread (there is no way to cancel a thread
    blocked in `Connection.recv_bytes()`) while this polls `_progress_signal`
    (on-disk index fingerprint plus the daemon's own live IndexingProgress
    counters -- see that function's docstring for why disk state alone
    isn't a reliable signal here). No change for `_STALL_TIMEOUT_S` means a
    genuine stall -- kill the daemon (which unblocks the stuck thread with a
    `RuntimeError`, since `index()`/`search()` both turn `EOFError` on the
    now-closed socket into one) and retry with a fresh daemon. Resuming is
    cheap and lossless: verified live (this session) that `ccc index`
    picks back up from its on-disk LMDB/SQLite state within ~40s of a
    kill, re-embedding nothing already completed.
    """
    for attempt in range(1, _MAX_STALL_RETRIES + 1):
        outcome: dict[str, object] = {}

        def _target() -> None:
            try:
                outcome["result"] = fn()
            except Exception as exc:  # noqa: BLE001 - re-raised on the caller's thread below
                outcome["error"] = exc

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()

        last_fp = _progress_signal(project_root)
        last_progress = time.monotonic()
        while thread.is_alive():
            thread.join(timeout=_STALL_POLL_INTERVAL_S)
            if not thread.is_alive():
                break
            fp = _progress_signal(project_root)
            now = time.monotonic()
            if fp != last_fp:
                last_fp = fp
                last_progress = now
            elif now - last_progress >= _STALL_TIMEOUT_S:
                break  # stalled -- fall through to kill+retry below

        if not thread.is_alive():
            if "error" in outcome:
                raise outcome["error"]  # type: ignore[misc]
            return outcome["result"]  # type: ignore[return-value]

        # Stalled: the background thread is still blocked inside the
        # daemon call (and stays abandoned -- daemon=True -- there is no
        # way to cancel it; killing the daemon below unblocks it shortly
        # after with an error, which is harmless since nothing reads
        # `outcome` for this attempt anymore).
        _kill_shared_daemon_hard()
        if attempt == _MAX_STALL_RETRIES:
            raise RuntimeError(
                f"cocoindex daemon stalled {attempt} time(s) in a row on "
                f"{project_root!r} with no on-disk progress for "
                f"{_STALL_TIMEOUT_S:.0f}s each time -- giving up. See "
                "~/.cocoindex_code/daemon.log."
            )
    raise AssertionError("unreachable")  # pragma: no cover


# --- multi-tenant routing (T028, specs/001-multi-version-serving) ---------
#
# Mirrors build/build/layout.py's `warm_search_dir(country, version)`
# convention (`data/warm/<country>/<version>/search`) rather than importing
# it: chunker is a separately deployable serving process (constitution
# Principle II -- build and serve are separate resource pools/processes),
# has no dependency today on the `build/` project (which is being built in
# parallel by another agent as of this change), and the two conventions
# must stay byte-identical regardless of which project changes first. If a
# tiny shared "layout" package is ever extracted, this should import that
# instead of hand-duplicating the convention -- tracked as a follow-up, not
# done here.
def _warm_search_dir(country: str, version: str) -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "data" / "warm" / country / version / "search"


def _invalid_warm_project_reason(path: Path) -> str | None:
    """None if `path` looks like a finished, servable cocoindex-code
    project; otherwise a caller-facing reason it isn't.

    Deliberately a pure filesystem check, not a daemon round-trip: a
    (country, version) that was requested but never built, or is still
    mid-build in a staging path (never promoted here), must fail clearly
    rather than silently opening a directory that doesn't have an index in
    it yet -- see contracts/build-serve-tools.md "no direct filesystem
    exposure" and constitution Principle II (a build MUST NOT be raced by a
    concurrent read of the same artifact; this check only ever looks at the
    *warm*, promoted path, never a staging path).
    """
    if not path.is_dir():
        return (
            f"No warm data found for this (country, version) -- expected a built"
            f" index at {path}. This pair has not been built yet. Request it via"
            " the build server's bcatlas_request_version tool (same country/"
            " version), then poll bcatlas_version_status until it reports ready"
            " before retrying this call."
        )
    try:
        from cocoindex_code.settings import target_sqlite_db_path

        db_path = target_sqlite_db_path(path)
    except Exception:
        db_path = path / ".cocoindex_code" / "target_sqlite.db"
    if not db_path.exists():
        return (
            f"{path} exists but has no finished index yet (missing"
            f" {db_path.name}) -- build may still be in progress. Poll the build"
            " server's bcatlas_version_status tool for this country/version and"
            " retry once it reports ready."
        )
    return None


# Submodule directories that only exist under the *default* multi-corpus
# project_root (`data/`, holding AL source + both docs corpora side by
# side). A routed per-(country, version) project_root is a raw checkout
# with no such prefix -- see `_corpus_path_prefixes` below.
_DEFAULT_CORPUS_PATH_PREFIX_CANDIDATES = ("w1-28-src", "docs", "docs-devitpro")


def _resolve_corpus_path_prefixes(project_root: str) -> tuple[str, ...]:
    """Which of the default corpus's known submodule subdirectories
    actually exist under `project_root`, computed once at server startup
    from the real directory rather than assumed.
    """
    root = Path(project_root)
    return tuple(p for p in _DEFAULT_CORPUS_PATH_PREFIX_CANDIDATES if (root / p).is_dir())


def _expand_paths_for_corpus_prefixes(paths: list[str], prefixes: tuple[str, ...]) -> list[str]:
    """Also try each glob under the corpus's submodule prefixes.

    `bcatlas_search`'s `paths` filter is matched via SQLite GLOB against
    the exact indexed relative path (cocoindex_code -- vendored, not
    modified here, constitution Principle VI). On the default corpus every
    indexed path carries a submodule prefix (e.g.
    `w1-28-src/Base Application/Sales/...`), so a caller-supplied glob like
    `['Base Application/Sales/*']` -- the natural, prefix-agnostic form,
    and the one the tool's own docstring example used to show -- silently
    matched zero rows instead of erroring. Expanding here to also try the
    prefixed form keeps both spellings working without touching the
    vendored query engine.
    """
    expanded = list(paths)
    for p in paths:
        if p.startswith(prefixes):
            continue
        expanded.extend(f"{prefix}/{p}" for prefix in prefixes)
    return expanded


# Operator-configurable MCP instructions/path-filter-prefix override, relative
# to `project_root` -- specs/006-configurable-mcp-instructions, issue #20.
# Deliberately not part of cocoindex_code's own ProjectSettings/settings.yml
# (constitution Principle VI -- that's vendored, unmodified); this is purely
# presentational and belongs entirely in code this repo owns.
_PRESENTATION_SETTINGS_REL_PATH = Path(".bcatlas") / "mcp_presentation.yml"


def _load_presentation_settings(project_root: str) -> tuple[str, tuple[str, ...] | None]:
    """Returns `(instructions, path_prefixes)`. `path_prefixes` is `None`
    when not explicitly configured (caller falls back to
    `_resolve_corpus_path_prefixes`'s dynamic detection) or a tuple
    (possibly empty, meaning "no prefixes") when configured.

    A missing settings file is not an error -- only a present-but-invalid
    one is (FR-005), same fail-fast precedent as `_validate_project_root`.
    """
    settings_path = Path(project_root) / _PRESENTATION_SETTINGS_REL_PATH
    if not settings_path.is_file():
        return _MCP_INSTRUCTIONS, None

    try:
        raw = yaml.safe_load(settings_path.read_text())
    except yaml.YAMLError as exc:
        raise SystemExit(f"error: invalid YAML in {settings_path}: {exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SystemExit(
            f"error: {settings_path} must contain a YAML mapping at the top"
            f" level, got {type(raw).__name__}"
        )

    instructions = raw.get("instructions", _MCP_INSTRUCTIONS)
    if not isinstance(instructions, str):
        raise SystemExit(
            f"error: {settings_path}: 'instructions' must be a string, got"
            f" {type(instructions).__name__}"
        )

    path_prefixes: tuple[str, ...] | None = None
    if "path_prefixes" in raw:
        raw_prefixes = raw["path_prefixes"]
        if not isinstance(raw_prefixes, list) or not all(isinstance(p, str) for p in raw_prefixes):
            raise SystemExit(f"error: {settings_path}: 'path_prefixes' must be a list of strings")
        path_prefixes = tuple(raw_prefixes)

    return instructions, path_prefixes


def create_filtered_mcp_server(project_root: str) -> FastMCP:
    """Like cocoindex_code.server.create_mcp_server, plus test-path filtering."""
    from cocoindex_code import client as _client

    _instructions, _configured_prefixes = _load_presentation_settings(project_root)
    mcp = FastMCP("cocoindex-code", instructions=_instructions)
    _corpus_path_prefixes = (
        _configured_prefixes
        if _configured_prefixes is not None
        else _resolve_corpus_path_prefixes(project_root)
    )

    @mcp.tool(
        name="bcatlas_search",
        description=(
            "Semantic search over Business Central's AL base-application"
            " source and developer docs -- finds code and docs by meaning,"
            " not just text matching."
            " Use this instead of grep/glob when you need to find how BC"
            " itself implements something, understand how a feature works,"
            " or locate related code/docs without knowing exact object,"
            " procedure, or event names."
            " Accepts natural language queries"
            " (e.g., 'sales order posting validation', 'outbound REST call"
            " from AL')"
            " or AL code snippets."
            " Returns matching code chunks with file paths,"
            " line numbers, and relevance scores."
            " Test codeunits (Tests-*, *Test Library*, etc.) are excluded by"
            " default since they usually outrank real implementations on"
            " textual similarity without being what you want -- pass"
            " include_tests=true to search them too."
            " Start with a small limit (e.g., 5);"
            " if most results look relevant, use offset to paginate for more."
            "\n\n"
            " This is embedding/meaning-based, not a literal grep -- it can"
            " miss or mis-rank an exact string, and results are chunked so"
            " you may not get the precise line you need. If you already know"
            " the exact object (table/page/codeunit/...) by name -- e.g. you"
            " just need one specific field, procedure, or line inside a table"
            " you can already name -- don't search for it: use"
            " bcatlas_get_object_source/bcatlas_get_signature/"
            " bcatlas_get_procedure_body (graph server tools) with that exact"
            " name instead and read the line you need out of the real"
            " source they return. Reserve this search tool for when you"
            " don't yet know which object/procedure has what you're looking"
            " for."
        ),
    )
    async def search(
        query: str = Field(
            description=(
                "Natural language query or AL code snippet to search for."
                " Examples: 'sales order posting validation',"
                " 'how are customers authenticated',"
                " 'outbound REST call from AL',"
                " or paste an AL snippet to find similar code."
            )
        ),
        limit: int = Field(
            default=5,
            ge=1,
            le=100,
            description="Maximum number of results to return (1-100)",
        ),
        offset: int = Field(
            default=0,
            ge=0,
            description="Number of results to skip for pagination",
        ),
        refresh_index: bool = Field(
            default=True,
            description=(
                "Whether to incrementally update the index before searching."
                " Set to False for faster consecutive queries"
                " when the codebase hasn't changed."
            ),
        ),
        languages: list[str] | None = Field(
            default=None,
            description="Filter by programming language(s). Example: ['python', 'typescript']",
        ),
        paths: list[str] | None = Field(
            default=None,
            description=(
                "Filter by file path pattern(s) using GLOB wildcards (* and ?)."
                " Example: ['Base Application/Sales/*']. On the default"
                " corpus, both that prefix-agnostic form and the fully"
                " qualified form (e.g."
                " ['w1-28-src/Base Application/Sales/*']) are matched --"
                " you don't need to know the internal w1-28-src/docs/"
                " docs-devitpro submodule layout."
            ),
        ),
        include_tests: bool = Field(
            default=False,
            description=(
                "Include AL test codeunits (Tests-*, *Test Library*, etc.) in"
                " results. Off by default -- test code usually isn't what you"
                " want when looking for a real implementation."
            ),
        ),
        country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') identifying a specific,"
                " already-built (country, version) pair to search instead of"
                " this server's default corpus. Must be supplied together"
                " with `version` -- resolve both first (e.g. via the"
                " registry server's discovery/resolve tools). Omit both to"
                " search the default corpus."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact resolved version string (e.g."
                " 'w1-28.2.50931.52151') identifying a specific,"
                " already-built (country, version) pair to search, paired"
                " with `country`. Omit both to search the default corpus."
            ),
        ),
    ) -> SearchResultModel:
        """Query the codebase index via the daemon, filtering out test paths by default."""
        loop = asyncio.get_event_loop()
        try:
            # Multi-tenant routing (T028): a caller that supplies neither
            # country nor version gets today's exact behavior unchanged --
            # the one project_root this server was started with, honoring
            # `refresh_index` as before. A caller that supplies both routes
            # to that specific (country, version) pair's warm search
            # directory instead. Historical (country, version) builds are
            # immutable once promoted (constitution Principle III), so
            # re-indexing them on every query would be wasted work and risks
            # racing an external build process's own writes to that same
            # path (Principle II) -- refresh_index is therefore only ever
            # honored for this server's own default/originally-configured
            # corpus, never for a routed (country, version).
            target_root = project_root
            if country is not None or version is not None:
                if not country or not version:
                    return SearchResultModel(
                        success=False,
                        message=(
                            "Both `country` and `version` must be supplied"
                            " together to search a specific (country,"
                            " version) pair. Resolve an exact version first"
                            " (e.g. via the registry server's"
                            " bcatlas_resolve_version tool), then pass both."
                        ),
                    )
                warm_dir = _warm_search_dir(country, version)
                invalid_reason = _invalid_warm_project_reason(warm_dir)
                if invalid_reason:
                    return SearchResultModel(success=False, message=invalid_reason)
                target_root = str(warm_dir)
                refresh_index = False

            effective_paths = paths
            if paths and target_root == project_root and _corpus_path_prefixes:
                effective_paths = _expand_paths_for_corpus_prefixes(paths, _corpus_path_prefixes)

            if refresh_index:
                await loop.run_in_executor(
                    None,
                    lambda: _run_with_stall_recovery(
                        lambda: _client.index(target_root), target_root
                    ),
                )

            if include_tests:
                resp = await loop.run_in_executor(
                    None,
                    lambda: _run_with_stall_recovery(
                        lambda: _client.search(
                            project_root=target_root,
                            query=query,
                            languages=languages,
                            paths=effective_paths,
                            limit=limit,
                            offset=offset,
                        ),
                        target_root,
                    ),
                )
                kept = resp.results
                message = resp.message
            else:
                # Test chunks outrank real implementations on raw textual
                # similarity often enough that a plain `limit`-sized fetch
                # can come back empty after filtering -- overfetch and widen
                # until we have enough or give up.
                fetch_limit = min(_MAX_FETCH_LIMIT, max(limit * _OVERFETCH_MULTIPLIER, 20))
                kept: list = []
                message = None
                for _ in range(_MAX_OVERFETCH_ROUNDS):
                    resp = await loop.run_in_executor(
                        None,
                        lambda fl=fetch_limit: _run_with_stall_recovery(
                            lambda: _client.search(
                                project_root=target_root,
                                query=query,
                                languages=languages,
                                paths=effective_paths,
                                limit=fl,
                                offset=offset,
                            ),
                            target_root,
                        ),
                    )
                    message = resp.message
                    kept = [r for r in resp.results if not _is_test_path(r.file_path)]
                    if len(kept) >= limit or fetch_limit >= _MAX_FETCH_LIMIT or len(resp.results) < fetch_limit:
                        break
                    fetch_limit = min(_MAX_FETCH_LIMIT, fetch_limit * _OVERFETCH_MULTIPLIER)
                kept = kept[:limit]

            return SearchResultModel(
                success=True,
                results=[
                    CodeChunkResult(
                        file_path=r.file_path,
                        language=r.language,
                        # W1-28's source is CRLF -- the raw \r survives into
                        # every chunk and costs a token per line for no
                        # information (AL isn't whitespace-sensitive).
                        content=r.content.replace("\r\n", "\n").replace("\r", "\n"),
                        start_line=r.start_line,
                        end_line=r.end_line,
                        # Full float precision (~17 sig figs) is meaningless
                        # for a similarity score and costs tokens for nothing.
                        score=round(r.score, 4),
                    )
                    for r in kept
                ],
                total_returned=len(kept),
                offset=offset,
                message=message,
            )
        except Exception as e:
            return SearchResultModel(success=False, message=f"Query failed: {e!s}")

    return mcp


def _validate_project_root(project_root: str) -> None:
    """Fail fast on a misconfigured AL source directory rather than starting
    up with a silently empty index (specs/005-local-source-directory,
    FR-004/FR-005 -- issue #18): a missing/non-directory path is a fatal
    config error, but an existing directory with zero `.al` files just gets
    a warning since an operator may intentionally point at a not-yet-
    populated directory before adding source.
    """
    root = Path(project_root)
    if not root.is_dir():
        raise SystemExit(
            f"error: configured AL source directory does not exist or is not"
            f" a directory: {root}"
        )
    if not any(root.rglob("*.al")):
        print(
            f"warning: {root} contains no .al files -- the index will be"
            " empty until AL source is added there.",
            flush=True,
        )


def _validate_watch_interval(value: float | None) -> None:
    """Fail fast on a nonsensical `--watch-interval-seconds` rather than
    silently spinning a zero/negative-delay loop (specs/007-file-watcher-
    reindex, FR-001 -- issue #21).
    """
    if value is not None and value <= 0:
        raise SystemExit(
            f"error: --watch-interval-seconds must be a positive number, got {value}"
        )


def _watch_reindex_once(project_root: str) -> None:
    """One watch-mode reindex attempt -- the exact same hardened call path
    `bcatlas_search`'s `refresh_index=True` already uses (research.md:
    reuse the existing verified primitive rather than a new one).
    """
    from cocoindex_code import client as _client

    _run_with_stall_recovery(lambda: _client.index(project_root), project_root)


async def _watch_loop(
    project_root: str,
    interval_s: float,
    reindex_once: Callable[[str], None] = _watch_reindex_once,
) -> None:
    """Background task: reindex `project_root` every `interval_s` seconds
    for the lifetime of the server process (specs/007-file-watcher-reindex,
    issue #21). All file changes landing within one interval are covered by
    the single next reindex call -- coalescing (FR-005) falls out of this
    for free, cocoindex's own incremental engine already processes every
    changed file in one `update()` pass, not one pass per file. A failed
    attempt is logged and the loop keeps going (FR-006) rather than crashing
    the server or silently going stale forever; `reindex_once` is injectable
    so tests can exercise this without a real daemon.
    """
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(interval_s)
        try:
            await loop.run_in_executor(None, reindex_once, project_root)
        except Exception as exc:  # noqa: BLE001 - logged and retried, never fatal
            print(
                f"warning: watch-mode reindex of {project_root} failed: {exc}",
                flush=True,
            )


async def _serve(args: argparse.Namespace) -> None:
    mcp_server = create_filtered_mcp_server(args.project_root)
    mcp_server.settings.host = args.host
    mcp_server.settings.port = args.port
    if args.watch_interval_seconds is not None:
        asyncio.create_task(_watch_loop(args.project_root, args.watch_interval_seconds))
        print(
            f"watch mode enabled -- reindexing {args.project_root} every"
            f" {args.watch_interval_seconds}s",
            flush=True,
        )
    print(f"cocoindex-code MCP server (streamable-http) on http://{args.host}:{args.port}/mcp")
    await mcp_server.run_streamable_http_async()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8801)
    parser.add_argument(
        "--watch-interval-seconds",
        type=float,
        default=None,
        help=(
            "Opt-in: reindex project_root every N seconds in the background,"
            " so changes are searchable without an explicit refresh (issue"
            " #21). Disabled (today's on-demand-only behavior) unless set."
        ),
    )
    args = parser.parse_args()

    _validate_project_root(args.project_root)
    _validate_watch_interval(args.watch_interval_seconds)
    asyncio.run(_serve(args))


if __name__ == "__main__":
    main()
