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
import re

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


def create_filtered_mcp_server(project_root: str) -> FastMCP:
    """Like cocoindex_code.server.create_mcp_server, plus test-path filtering."""
    from cocoindex_code import client as _client

    mcp = FastMCP("cocoindex-code", instructions=_MCP_INSTRUCTIONS)

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
                " Example: ['src/utils/*', '*.py']"
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
    ) -> SearchResultModel:
        """Query the codebase index via the daemon, filtering out test paths by default."""
        loop = asyncio.get_event_loop()
        try:
            if refresh_index:
                await loop.run_in_executor(None, lambda: _client.index(project_root))

            if include_tests:
                resp = await loop.run_in_executor(
                    None,
                    lambda: _client.search(
                        project_root=project_root,
                        query=query,
                        languages=languages,
                        paths=paths,
                        limit=limit,
                        offset=offset,
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
                        lambda fl=fetch_limit: _client.search(
                            project_root=project_root,
                            query=query,
                            languages=languages,
                            paths=paths,
                            limit=fl,
                            offset=offset,
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
