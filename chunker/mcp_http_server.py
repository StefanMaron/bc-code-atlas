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
_STALL_TIMEOUT_S = 90.0
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


def _kill_shared_daemon_hard() -> None:
    """SIGTERM -> SIGKILL the shared `ccc` daemon by its own pidfile.

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
    if pid is not None:
        for sig, grace_s in ((signal.SIGTERM, 10.0), (signal.SIGKILL, 5.0)):
            try:
                os.killpg(pid, sig)
            except ProcessLookupError:
                break
            except PermissionError:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    break
            deadline = time.monotonic() + grace_s
            gone = False
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    gone = True
                    break
                time.sleep(0.2)
            if gone:
                break
    _daemon_client._daemon_ensured = False


def _run_with_stall_recovery(fn: Callable[[], _T], project_root: str) -> _T:
    """Run a blocking `cocoindex_code.client` call (`index`/`search`) to
    completion, recovering from the documented daemon stall above instead
    of hanging forever.

    `fn` runs on a background thread (there is no way to cancel a thread
    blocked in `Connection.recv_bytes()`) while this polls the on-disk
    index fingerprint. No change for `_STALL_TIMEOUT_S` means a genuine
    stall -- kill the daemon (which unblocks the stuck thread with a
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

        last_fp = _index_state_fingerprint(project_root)
        last_progress = time.monotonic()
        while thread.is_alive():
            thread.join(timeout=_STALL_POLL_INTERVAL_S)
            if not thread.is_alive():
                break
            fp = _index_state_fingerprint(project_root)
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
        return f"No warm data found for this (country, version) -- expected a built index at {path}."
    try:
        from cocoindex_code.settings import target_sqlite_db_path

        db_path = target_sqlite_db_path(path)
    except Exception:
        db_path = path / ".cocoindex_code" / "target_sqlite.db"
    if not db_path.exists():
        return (
            f"{path} exists but has no finished index yet (missing"
            f" {db_path.name}) -- build may still be in progress."
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


def create_filtered_mcp_server(project_root: str) -> FastMCP:
    """Like cocoindex_code.server.create_mcp_server, plus test-path filtering."""
    from cocoindex_code import client as _client

    mcp = FastMCP("cocoindex-code", instructions=_MCP_INSTRUCTIONS)
    _corpus_path_prefixes = _resolve_corpus_path_prefixes(project_root)

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8801)
    args = parser.parse_args()

    mcp_server = create_filtered_mcp_server(args.project_root)
    mcp_server.settings.host = args.host
    mcp_server.settings.port = args.port
    print(f"cocoindex-code MCP server (streamable-http) on http://{args.host}:{args.port}/mcp")
    asyncio.run(mcp_server.run_streamable_http_async())


if __name__ == "__main__":
    main()
