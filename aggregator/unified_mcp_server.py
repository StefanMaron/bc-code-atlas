"""Single unified MCP endpoint for bc-code-atlas.

Testers only need to point their MCP client at one URL. This process presents
one `/mcp` endpoint and transparently forwards each tool call to whichever of
the two backend servers actually implements it:

  - the search server (chunker/mcp_http_server.py, default :8801) -- semantic
    search over the AL source + docs corpus.
  - the graph server (tools/graphify-al's `graphify.serve`, default :8802) --
    the structural call/subscribe/extend graph.

Both backends keep running exactly as documented in the top-level README --
this is a thin proxy, not a reimplementation. No business logic lives here;
if a backend's behavior changes, this file doesn't need to.

Usage:
    uv run python unified_mcp_server.py \
        --search-url http://127.0.0.1:8801/mcp \
        --graph-url http://127.0.0.1:8802/mcp \
        --port 8800
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from pydantic import Field

_AGGREGATOR_INSTRUCTIONS = (
    "A queryable window into Microsoft Dynamics 365 Business Central's AL"
    " source code and official documentation -- for dependency and"
    " implementation investigation before writing or reviewing AL"
    " customizations."
    "\n\n"
    "Indexed corpus: the w1-28 base application source (extracted from"
    " Microsoft's own build, not decompiled) plus the public"
    " dynamics365smb-devitpro developer docs."
    "\n\n"
    "Two complementary ways to query it:"
    "\n"
    "- `search` -- semantic search by meaning. Use this first to find a"
    " starting point: real implementations, base-application objects,"
    " call-site examples, or doc pages, even when you don't know the exact"
    " object/procedure/event name."
    "\n"
    "- `query_graph`, `get_node`, `get_neighbors`, `get_community`,"
    " `god_nodes`, `graph_stats`, `shortest_path` -- the exact structural"
    " relationship graph (objects, procedures, event subscriptions,"
    " extension targets) with real call/subscribe/extend edges extracted"
    " from source. Use these once you have a concrete node to trace: what"
    " calls or subscribes to it, what it extends, or how two BC concepts"
    " connect."
    "\n\n"
    "A good pattern: `search` for a concept in natural language, then"
    " `query_graph` or `get_neighbors` on what it finds to see its exact"
    " connections."
)


@asynccontextmanager
async def _backend_session(url: str):
    async with streamablehttp_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def _forward(url: str, tool: str, arguments: dict[str, Any]) -> Any:
    """Call `tool` on the backend at `url` and return its result verbatim.

    Prefers structured content (e.g. the search tool's SearchResultModel) so
    clients get the same shape they'd get calling the backend directly; falls
    back to concatenated text content for the graph server's plain-text tools.
    """
    # Backends' JSON schemas mark optional array/string params as simply
    # absent-when-unset, not nullable -- forwarding an explicit `None` (which
    # FastMCP always includes for unset Optional[...] Field() params) fails
    # their input validation. Omit anything unset instead of nulling it.
    arguments = {k: v for k, v in arguments.items() if v is not None}
    async with _backend_session(url) as session:
        result = await session.call_tool(tool, arguments)
    text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
    if result.isError:
        raise RuntimeError(text or f"{tool} failed with no error detail")
    if result.structuredContent is not None:
        return result.structuredContent
    return text


def create_aggregator(search_url: str, graph_url: str) -> FastMCP:
    mcp = FastMCP("bc-code-atlas", instructions=_AGGREGATOR_INSTRUCTIONS)

    @mcp.tool(
        name="search",
        description=(
            "Semantic search over Business Central's AL base-application"
            " source and developer docs -- finds code and docs by meaning,"
            " not just text matching. Use this to find how BC itself"
            " implements something (e.g. posting logic, a table/page/"
            " codeunit you're extending, an API pattern), locate real"
            " call-site examples, or check official docs, before guessing"
            " at AL syntax or event names from training data alone."
            " Test codeunits (Tests-*, *Test Library*, etc.) are excluded"
            " by default -- pass include_tests=true to search them too."
            " Start with a small limit (e.g. 5); if most results look"
            " relevant, use offset to paginate for more."
        ),
    )
    async def search(
        query: str = Field(
            description=(
                "Natural language query or code snippet. Examples:"
                " 'sales order posting validation', 'how are customers"
                " authenticated', 'outbound REST call from AL', or paste"
                " an AL snippet to find similar code."
            )
        ),
        limit: int = Field(default=5, ge=1, le=100, description="Max results (1-100)"),
        offset: int = Field(default=0, ge=0, description="Results to skip, for pagination"),
        refresh_index: bool = Field(
            default=True,
            description="Incrementally update the index before searching. False = faster repeat queries.",
        ),
        languages: list[str] | None = Field(default=None, description="Filter by language(s), e.g. ['al']"),
        paths: list[str] | None = Field(
            default=None,
            description="Filter by file path glob(s), e.g. ['Base Application/Sales/*']",
        ),
        include_tests: bool = Field(
            default=False,
            description="Include AL test codeunits (Tests-*, *Test Library*, etc.) in results.",
        ),
    ) -> Any:
        return await _forward(
            search_url,
            "search",
            {
                "query": query,
                "limit": limit,
                "offset": offset,
                "refresh_index": refresh_index,
                "languages": languages,
                "paths": paths,
                "include_tests": include_tests,
            },
        )

    @mcp.tool(
        name="query_graph",
        description=(
            "Search Business Central's structural knowledge graph (objects,"
            " procedures, event subscriptions, extension targets, real"
            " call/subscribe/extend edges) using BFS or DFS. Returns"
            " relevant nodes and edges as text context. Example question:"
            " 'what subscribes to OnBeforePostSalesDoc' or 'what does"
            " Codeunit 80 call'."
        ),
    )
    async def query_graph(
        question: str = Field(description="Natural language question or keyword search"),
        mode: str = Field(default="bfs", description="bfs=broad context, dfs=trace a specific path"),
        depth: int = Field(default=3, description="Traversal depth (1-6)"),
        token_budget: int = Field(default=6000, description="Max output tokens"),
        context_filter: list[str] | None = Field(
            default=None, description="Optional explicit edge-context filter, e.g. ['call', 'field']"
        ),
    ) -> Any:
        return await _forward(
            graph_url,
            "query_graph",
            {
                "question": question,
                "mode": mode,
                "depth": depth,
                "token_budget": token_budget,
                "context_filter": context_filter,
            },
        )

    @mcp.tool(
        name="get_node",
        description="Get full details for a specific BC object/procedure node by label or ID.",
    )
    async def get_node(label: str = Field(description="Node label or ID to look up")) -> Any:
        return await _forward(graph_url, "get_node", {"label": label})

    @mcp.tool(
        name="get_neighbors",
        description=(
            "Get all direct neighbors of a BC object/procedure node with edge"
            " details -- e.g. everything that calls or subscribes to it, and"
            " everything it references."
        ),
    )
    async def get_neighbors(
        label: str = Field(description="Node label or ID to look up"),
        relation_filter: str | None = Field(default=None, description="Optional: filter by relation type"),
    ) -> Any:
        return await _forward(graph_url, "get_neighbors", {"label": label, "relation_filter": relation_filter})

    @mcp.tool(name="get_community", description="Get all nodes in a graph community by community ID.")
    async def get_community(community_id: int = Field(description="Community ID (0-indexed by size)")) -> Any:
        return await _forward(graph_url, "get_community", {"community_id": community_id})

    @mcp.tool(
        name="god_nodes",
        description="Return the most connected nodes -- the core abstractions of the base application.",
    )
    async def god_nodes(top_n: int = Field(default=10)) -> Any:
        return await _forward(graph_url, "god_nodes", {"top_n": top_n})

    @mcp.tool(
        name="graph_stats",
        description="Summary statistics for the structural graph: node count, edge count, communities, confidence breakdown.",
    )
    async def graph_stats() -> Any:
        return await _forward(graph_url, "graph_stats", {})

    @mcp.tool(
        name="shortest_path",
        description=(
            "Find the shortest structural path between two BC concepts --"
            " e.g. how a table field's value ends up propagated into a"
            " posted ledger entry."
        ),
    )
    async def shortest_path(
        source: str = Field(description="Source concept label or keyword"),
        target: str = Field(description="Target concept label or keyword"),
        max_hops: int = Field(default=8, description="Maximum hops to consider"),
    ) -> Any:
        return await _forward(graph_url, "shortest_path", {"source": source, "target": target, "max_hops": max_hops})

    return mcp


def main() -> None:
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--search-url", default="http://127.0.0.1:8801/mcp")
    parser.add_argument("--graph-url", default="http://127.0.0.1:8802/mcp")
    parser.add_argument(
        "--public-hostname",
        default=os.environ.get("AGGREGATOR_PUBLIC_HOSTNAME"),
        help=(
            "Public hostname this server is reachable at behind a reverse"
            " proxy/tunnel (env: AGGREGATOR_PUBLIC_HOSTNAME). The MCP"
            " transport's DNS-rebinding protection only allows"
            " Host: localhost/127.0.0.1 by default -- a tunnel forwards the"
            " original public Host header unchanged, so without this every"
            " request gets rejected with 'Invalid Host header' before it"
            " even reaches a tool."
        ),
    )
    args = parser.parse_args()

    mcp_server = create_aggregator(args.search_url, args.graph_url)
    mcp_server.settings.host = args.host
    mcp_server.settings.port = args.port
    if args.public_hostname:
        from mcp.server.transport_security import TransportSecuritySettings

        base = mcp_server.settings.transport_security
        mcp_server.settings.transport_security = TransportSecuritySettings(
            allowed_hosts=[*base.allowed_hosts, args.public_hostname, f"{args.public_hostname}:*"],
            allowed_origins=[*base.allowed_origins, f"https://{args.public_hostname}", f"http://{args.public_hostname}"],
        )
    print(f"bc-code-atlas unified MCP server (streamable-http) on http://{args.host}:{args.port}/mcp")
    print(f"  search  -> {args.search_url}")
    print(f"  graph   -> {args.graph_url}")
    if args.public_hostname:
        print(f"  public hostname allow-listed -> {args.public_hostname}")
    asyncio.run(mcp_server.run_streamable_http_async())


if __name__ == "__main__":
    main()
