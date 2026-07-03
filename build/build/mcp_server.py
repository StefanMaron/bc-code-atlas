"""MCP-over-HTTP server for the build/serve split (T027).

Exposes exactly the two tools contracts/build-serve-tools.md defines:
`bcatlas_request_version` (FR-011: immediate acknowledgment, distinct from
final results, when a (country, version) isn't already warm -- coalesced via
`queue.BuildQueue` per FR-017) and `bcatlas_version_status` (FR-012: a
caller polling too early gets a clear state, never partial/wrong-version
data).

Style matches `chunker/mcp_http_server.py`: a `FastMCP` instance built by a
`create_*_server()` factory (so tests/other entry points can construct one
without going through `main()`/argparse), served over Streamable HTTP, with
domain-specific instructions naming the real corpus rather than generic
boilerplate (constitution Principle VII).

Version-spec resolution: this project deliberately never imports
cocoindex-code in-process (see `pyproject.toml`), but DOES depend on
`registry` (a path dependency, read-only -- see `pyproject.toml`'s
[tool.uv.sources]) for `registry.resolver.resolve_version`, which had
already landed by the time this file was written. No duplicate/local
resolution logic was needed as a result -- see this module's git history if
`registry.resolver` is ever unavailable in a future checkout for context on
why this import exists.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from registry import git_ops
from registry.resolver import ResolutionFailure, ResolvedVersion, UpstreamUnavailableError, resolve_version

from . import incremental, layout, promote
from .queue import BuildQueue
from .warm_index import list_warm_versions

_MCP_INSTRUCTIONS = (
    "Build/serve control plane for Microsoft Dynamics 365 Business Central's"
    " AL source -- makes an arbitrary (country, version) pair's semantic"
    " search + structural graph data available on demand, building it from"
    " the real upstream source-history repository if it isn't already warm."
    "\n\n"
    "Usage order: resolve a (country, version) with the registry server's"
    " bcatlas_resolve_version first (or supply a spec directly here --"
    " resolution happens internally either way), then call"
    " bcatlas_request_version. If it comes back status=ready, the search and"
    " graph MCP tools are usable immediately for that (country, version)."
    " Otherwise a build was started (or an already-in-flight one for the"
    " same pair was reused) -- poll bcatlas_version_status until state is"
    " ready before querying, never assume it's done just because time has"
    " passed."
    "\n\n"
    "Building a never-before-seen version is real work (embedding + graph"
    " extraction against real source) and can take real time -- from"
    " roughly a minute to several minutes depending on how much can be"
    " reused from an already-warm sibling version, not something to expect"
    " back instantly."
)

_ETA_HINT = (
    "A cold build (no similar warm sibling to reuse) re-embeds the full"
    " corpus and can take several minutes; an incremental build from an"
    " already-warm same-country or high-overlap sibling is typically much"
    " faster. Poll bcatlas_version_status rather than guessing a fixed wait."
)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def create_build_server(
    data_dir: Path = layout.DEFAULT_DATA_DIR,
    mirror_dir: Path = git_ops.DEFAULT_MIRROR_DIR,
    max_concurrent: int | None = None,
) -> FastMCP:
    """Build a fresh `FastMCP` instance wired to its own `BuildQueue`. A
    factory (not a module-level singleton) so tests can construct several
    independent servers/queues without cross-talk.
    """
    from .queue import DEFAULT_MAX_CONCURRENT_BUILDS

    queue = BuildQueue(max_concurrent=max_concurrent or DEFAULT_MAX_CONCURRENT_BUILDS)
    mcp = FastMCP("bc-code-atlas-build", instructions=_MCP_INSTRUCTIONS)

    def _do_build_and_promote(country: str, commit_sha: str, version_string: str, build_id: str) -> None:
        """Blocking: real build (git fetch/diff/patch, `ccc index`,
        `graphify update`) followed by atomic promotion. Runs in a worker
        thread (`asyncio.to_thread` below) -- never call directly from a
        coroutine.
        """
        try:
            incremental.build_version(
                country=country,
                commit_sha=commit_sha,
                version_string=version_string,
                build_id=build_id,
                data_dir=data_dir,
                mirror_dir=mirror_dir,
            )
            promote.promote(build_id, country, commit_sha, data_dir=data_dir)
        except Exception:
            promote.discard(build_id, data_dir=data_dir)
            raise

    @mcp.tool(
        name="bcatlas_request_version",
        description=(
            "Request that a (country, version) pair's search + graph data"
            " become available, building it from real upstream source if"
            " it isn't warm yet. Returns immediately -- status=ready means"
            " usable now; status=queued/in_progress means a build was"
            " started or an identical in-flight request was reused, poll"
            " bcatlas_version_status for completion. Never blocks until the"
            " build finishes."
        ),
    )
    async def bcatlas_request_version(
        country: str = Field(description="Country/localization code, e.g. 'w1', 'us', 'de'."),
        spec: str = Field(
            description=(
                "Exact version string (e.g. 'w1-28.2.50931.52151'), exact commit"
                " sha, or a loose 'major.minor' spec (e.g. '28.1') resolved to"
                " its newest matching build."
            )
        ),
    ) -> dict:
        try:
            resolved = await asyncio.to_thread(resolve_version, country, spec, mirror_dir, git_ops.UPSTREAM_URL)
        except UpstreamUnavailableError as e:
            return {"error": "upstream_unavailable", "detail": str(e)}

        if isinstance(resolved, ResolutionFailure):
            return {"resolved": False, "reason": resolved.reason, "detail": resolved.detail}

        assert isinstance(resolved, ResolvedVersion)
        commit_sha = resolved.commit_sha
        version_string = resolved.version_string

        warm_path = layout.warm_root(country, commit_sha, data_dir)
        if warm_path.is_dir():
            return {
                "status": "ready",
                "country": country,
                "commit_sha": commit_sha,
                "served_since": _iso(warm_path.stat().st_mtime),
            }

        key = (country, commit_sha)
        build_id = promote.new_build_id(country, commit_sha)

        def build_fn() -> "asyncio.Future[None]":
            return asyncio.to_thread(_do_build_and_promote, country, commit_sha, version_string, build_id)

        record = await queue.request_build(key, build_fn)
        return {
            "status": record.state,
            "country": country,
            "commit_sha": commit_sha,
            "eta_hint": _ETA_HINT,
        }

    @mcp.tool(
        name="bcatlas_version_status",
        description=(
            "Poll build status for a (country, commit_sha) pair previously"
            " requested via bcatlas_request_version. state is 'unknown' for"
            " a commit never requested, 'queued'/'in_progress' while"
            " building, 'ready' once search/graph tools can be used against"
            " it, or 'failed' if the build errored (request it again to"
            " retry -- a failed build is never silently resumed)."
        ),
    )
    async def bcatlas_version_status(
        country: str = Field(description="Country/localization code, e.g. 'w1'."),
        commit_sha: str = Field(description="Exact commit sha returned by bcatlas_request_version."),
    ) -> dict:
        warm_path = layout.warm_root(country, commit_sha, data_dir)
        if warm_path.is_dir():
            return {"state": "ready"}
        return {"state": queue.status((country, commit_sha))}

    @mcp.tool(
        name="bcatlas_list_warm_versions",
        description=(
            "List every (country, version) pair that's already warm and"
            " instantly queryable right now -- no build wait. Check this"
            " before calling bcatlas_request_version: a nearby already-warm"
            " version is sometimes good enough, and costs nothing to use"
            " versus waiting minutes for an exact-match build. Sorted"
            " most-recently-touched first within each country."
        ),
    )
    async def bcatlas_list_warm_versions(
        country: str | None = Field(
            default=None,
            description="Restrict to one country/localization code, e.g. 'w1'. Omit for every country.",
        ),
    ) -> dict:
        versions = await asyncio.to_thread(list_warm_versions, data_dir, mirror_dir, git_ops.UPSTREAM_URL, country)
        return {"versions": versions}

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("BUILD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BUILD_PORT", "8804")))
    parser.add_argument("--data-dir", default=str(layout.DEFAULT_DATA_DIR))
    parser.add_argument("--mirror-dir", default=str(git_ops.DEFAULT_MIRROR_DIR))
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=None,
        help="Max concurrent GPU-bound builds (default: BCATLAS_BUILD_MAX_CONCURRENT env var, or 1).",
    )
    args = parser.parse_args()

    mcp_server = create_build_server(
        data_dir=Path(args.data_dir),
        mirror_dir=Path(args.mirror_dir),
        max_concurrent=args.max_concurrent,
    )
    mcp_server.settings.host = args.host
    mcp_server.settings.port = args.port
    print(f"bc-code-atlas build MCP server (streamable-http) on http://{args.host}:{args.port}/mcp")
    asyncio.run(mcp_server.run_streamable_http_async())


if __name__ == "__main__":
    main()
