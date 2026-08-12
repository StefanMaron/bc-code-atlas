"""Single unified MCP endpoint for bc-code-atlas.

Testers only need to point their MCP client at one URL. This process presents
one `/mcp` endpoint and transparently forwards each tool call to whichever of
the two backend servers actually implements it:

  - the search server (chunker/mcp_http_server.py, default :8801) -- semantic
    search over the AL source + docs corpus.
  - the graph server (tools/graphify-al's `graphify.serve`, default :8802) --
    the structural call/subscribe/extend graph.
  - the registry server (registry/registry/mcp_server.py, default :8803) --
    country/version discovery and resolution.
  - the build server (build/build/mcp_server.py, default :8804) -- on-demand
    build/serve of a (country, version) pair not yet warm.

All four backends keep running exactly as documented in the top-level README
-- this is a thin proxy, not a reimplementation. No business logic lives
here; if a backend's behavior changes, this file doesn't need to.

The search/graph backends are multi-tenant (specs/001-multi-version-serving):
every one of their tools accepts optional `country`/`version` params to
route to a specific already-built (country, version) pair instead of the
server's own default corpus. This file forwards those two params through
unchanged when supplied -- it does not resolve or validate them itself; the
backends already return a clear error for an unbuilt or partially-specified
pair (see chunker/mcp_http_server.py's `_invalid_warm_project_reason` and
graphify-al's `_resolve_ctx`), so a second check here would just duplicate
that logic. IMPORTANT: `version` here means the exact `commit_sha` returned
by `bcatlas_resolve_version`/`bcatlas_request_version` -- NOT the
human-readable `version_string` (e.g. `w1-28.2.50931.52151`) also returned
by those tools. The warm-directory layout (build/build/layout.py) is keyed
by commit_sha; passing version_string instead will look like a "not built
yet" error even for an already-warm pair.

Usage:
    uv run python unified_mcp_server.py \
        --search-url http://127.0.0.1:8801/mcp \
        --graph-url http://127.0.0.1:8802/mcp \
        --registry-url http://127.0.0.1:8803/mcp \
        --build-url http://127.0.0.1:8804/mcp \
        --port 8800
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from pydantic import Field

_log = logging.getLogger("bc_code_atlas.aggregator")

_AGGREGATOR_INSTRUCTIONS = (
    "A queryable window into Microsoft Dynamics 365 Business Central's AL"
    " source code and official documentation -- for dependency and"
    " implementation investigation before writing or reviewing AL"
    " customizations."
    "\n\n"
    "Indexed corpus: the w1-28 base application source (extracted from"
    " Microsoft's own build, not decompiled), the public functional/admin"
    " BC docs, and the public AL developer/compiler reference (diagnostics,"
    " properties, methods)."
    "\n\n"
    "Everything below defaults to the w1-28 base application unless you"
    " resolve and pass a different (country, version) -- see step 0."
    "\n\n"
    "0. `bcatlas_list_countries`, `bcatlas_list_versions`, `bcatlas_resolve_version`"
    " -- discover what countries/versions exist and resolve a spec (exact or"
    " loose, e.g. 'latest 28.1') to one unambiguous build. Check"
    " `bcatlas_list_warm_versions` first -- it's free and instant, and a"
    " nearby already-warm version is sometimes good enough instead of"
    " waiting on a fresh build. Otherwise call `bcatlas_request_version` to"
    " make a not-yet-built pair available (poll `bcatlas_version_status`),"
    " and pass the returned `commit_sha` (NOT `version_string`) as `version`"
    " to any search/graph tool below to query that exact pair instead of"
    " the default w1-28 corpus."
    "\n\n"
    "Three complementary layers, meant to be used in this order:"
    "\n"
    "1. `bcatlas_search` -- semantic search by meaning. Use this first to find a"
    " starting point: real implementations, base-application objects,"
    " call-site examples, or doc pages, even when you don't know the exact"
    " object/procedure/event name. If you already know the exact"
    " object/procedure name, skip straight to step 3 instead -- it's exact"
    " and cheaper than searching for something you can already name."
    "\n"
    "2. `bcatlas_query_graph`, `bcatlas_get_node`, `bcatlas_get_neighbors`, `bcatlas_get_community`,"
    " `bcatlas_god_nodes`, `bcatlas_graph_stats`, `bcatlas_shortest_path` -- the exact structural"
    " relationship graph (objects, procedures, event subscriptions,"
    " extension targets) with real call/subscribe/extend edges extracted"
    " from source. Use these once you have a concrete node to trace: what"
    " calls or subscribes to it, what it extends, or how two BC concepts"
    " connect."
    "\n"
    "3. `bcatlas_get_signature`, `bcatlas_get_procedure_body`, `bcatlas_get_object_source` -- exact"
    " source text re-read from the real source files for a node the previous"
    " two steps found. Use `bcatlas_get_signature` as a cheap check that you have"
    " the right node, then `bcatlas_get_procedure_body`/`bcatlas_get_object_source` to"
    " verify real behavior (exact params, var types, line-by-line logic)"
    " instead of guessing it from the name alone."
    "\n\n"
    "For comparing two versions of the same country: `bcatlas_diff` (scoped to"
    " a file path or a resolved symbol -- never unscoped) and"
    " `bcatlas_symbol_history` (every point within a range where one"
    " specific symbol's own text actually changed, not just its containing"
    " file)."
    "\n\n"
    "A good pattern: `bcatlas_search` for a concept in natural language, then"
    " `bcatlas_query_graph`/`bcatlas_get_neighbors` on what it finds to see its exact"
    " connections, then `bcatlas_get_signature`/`bcatlas_get_procedure_body` on the"
    " strongest candidate(s) to confirm the real implementation before"
    " answering or writing code against it."
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
    # Every call gets a short correlation id so a single request can be
    # traced across this log's start/end lines -- without this, an incident
    # report (e.g. a client-reported crash against the public tunnel
    # endpoint) is untraceable after the fact since nothing here previously
    # carried a timestamp or any way to line up which call was which.
    request_id = uuid.uuid4().hex[:8]
    started = time.monotonic()
    _log.info("[%s] -> %s %s args=%r", request_id, tool, url, arguments)
    try:
        async with _backend_session(url) as session:
            result = await session.call_tool(tool, arguments)
    except Exception:
        elapsed_ms = (time.monotonic() - started) * 1000
        _log.exception("[%s] <- %s %s raised after %.1fms", request_id, tool, url, elapsed_ms)
        raise
    elapsed_ms = (time.monotonic() - started) * 1000
    text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
    if result.isError:
        _log.warning("[%s] <- %s %s backend error after %.1fms: %s", request_id, tool, url, elapsed_ms, text[:300])
        raise RuntimeError(text or f"{tool} failed with no error detail")
    _log.info("[%s] <- %s %s ok after %.1fms", request_id, tool, url, elapsed_ms)
    if result.structuredContent is not None:
        return result.structuredContent
    return text


def create_aggregator(search_url: str, graph_url: str, registry_url: str, build_url: str) -> FastMCP:
    mcp = FastMCP("bc-code-atlas", instructions=_AGGREGATOR_INSTRUCTIONS)

    @mcp.tool(
        name="bcatlas_search",
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
            "\n\n"
            "This is embedding/meaning-based, not a literal grep -- it can"
            " miss or mis-rank an exact string, and results are chunked so"
            " you may not get the precise line you need. If you already"
            " know the exact object (table/page/codeunit/...) by name --"
            " e.g. you just need one specific field, procedure, or line"
            " inside a table you can already name -- don't search for it:"
            " call `bcatlas_get_object_source` with that object's name"
            " directly and read the line you need out of the real source it"
            " returns (individual table fields aren't their own graph"
            " nodes, so there's no narrower lookup than the whole object)."
            " Use `bcatlas_get_signature`/`bcatlas_get_procedure_body`"
            " instead when what you know the name of is a specific"
            " procedure/trigger, not the whole object. Reserve this search"
            " tool for when you don't yet know which object/procedure has"
            " what you're looking for."
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
            description=(
                "Filter by file path glob(s), e.g. ['Base Application/Sales/*']."
                " On the default corpus, both that prefix-agnostic form and"
                " the fully qualified form (e.g."
                " ['w1-28-src/Base Application/Sales/*']) are matched --"
                " you don't need to know the internal submodule layout."
            ),
        ),
        include_tests: bool = Field(
            default=False,
            description="Include AL test codeunits (Tests-*, *Test Library*, etc.) in results.",
        ),
        country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') to query a specific, already-built"
                " (country, version) pair instead of the default w1-28 corpus."
                " Must be paired with `version`. `bcatlas_resolve_version` alone"
                " only identifies the commit_sha -- it does NOT build or warm"
                " anything. This pair is only queryable once"
                " `bcatlas_request_version` returns status=ready (or"
                " `bcatlas_version_status` reports state=ready after polling)."
                " Passing an identified-but-unbuilt commit_sha here fails, not"
                " falls back to the default corpus."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact `commit_sha` (NOT `version_string`) from"
                " bcatlas_resolve_version/bcatlas_request_version, paired with"
                " `country`."
            ),
        ),
    ) -> Any:
        return await _forward(
            search_url,
            "bcatlas_search",
            {
                "query": query,
                "limit": limit,
                "offset": offset,
                "refresh_index": refresh_index,
                "languages": languages,
                "paths": paths,
                "include_tests": include_tests,
                "country": country,
                "version": version,
            },
        )

    @mcp.tool(
        name="bcatlas_query_graph",
        description=(
            "Search Business Central's structural knowledge graph (objects,"
            " procedures, event subscriptions, extension targets, real"
            " call/subscribe/extend edges) using BFS or DFS. Returns"
            " relevant nodes and edges as text context. Example question:"
            " 'what subscribes to OnBeforePostSalesDoc' or 'what does"
            " Codeunit 80 call'."
            "\n\n"
            "Only use this when you already have a specific, correctly-named"
            " symbol to trace from -- it does a broad BFS/DFS over the whole"
            " graph and a vague question (e.g. a general topic instead of an"
            " exact object/procedure/event name) returns a large, mostly"
            " irrelevant subgraph that burns tokens without answering the"
            " question. If you don't already know the exact name, call"
            " `bcatlas_search` first to find it, then use `bcatlas_get_node`/`bcatlas_get_neighbors`"
            " on that exact label instead of `bcatlas_query_graph` -- cheaper and far"
            " more precise for that case."
        ),
    )
    async def query_graph(
        question: str = Field(description="Natural language question or keyword search"),
        mode: str = Field(default="bfs", description="bfs=broad context, dfs=trace a specific path"),
        depth: int = Field(default=3, description="Traversal depth (1-6)"),
        token_budget: int = Field(default=6000, description="Max output tokens"),
        context_filter: list[str] | None = Field(
            default=None,
            description=(
                "Optional explicit edge-context filter, e.g. ['call', 'field']."
                " Also accepts 'cross_app', a structural filter (not a"
                " relation kind) that keeps only edges whose two endpoints"
                " belong to different apps (AL corpora only) -- combine it"
                " with a relation kind (e.g. ['cross_app', 'call']) to see"
                " only cross-app calls, or use it alone for every cross-app"
                " edge regardless of kind."
            ),
        ),
        country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') for a specific (country,"
                " version) pair's graph instead of the default. Must be"
                " paired with `version`."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact `commit_sha` (NOT `version_string`) from"
                " bcatlas_resolve_version/bcatlas_request_version, paired"
                " with `country`."
            ),
        ),
    ) -> Any:
        return await _forward(
            graph_url,
            "bcatlas_query_graph",
            {
                "question": question,
                "mode": mode,
                "depth": depth,
                "token_budget": token_budget,
                "context_filter": context_filter,
                "country": country,
                "version": version,
            },
        )

    @mcp.tool(
        name="bcatlas_get_node",
        description="Get full details for a specific BC object/procedure node by label or ID.",
    )
    async def get_node(
        label: str = Field(description="Node label or ID to look up"),
        country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') for a specific (country,"
                " version) pair's graph instead of the default. Must be"
                " paired with `version`."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact `commit_sha` (NOT `version_string`) from"
                " bcatlas_resolve_version/bcatlas_request_version, paired"
                " with `country`."
            ),
        ),
    ) -> Any:
        return await _forward(graph_url, "bcatlas_get_node", {"label": label, "country": country, "version": version})

    @mcp.tool(
        name="bcatlas_find_by_global_id",
        description=(
            "Cross-graph federation lookup: find the node(s) on a graph"
            " matching a `global_id` value copied from another graph's"
            " bcatlas_get_node output. `global_id` is a deterministic join"
            " key stamped on every AL node (real objects and external stubs"
            " alike), so a stub for object X in one corpus and the real X"
            " node in its own corpus share the same value -- use this to"
            " bridge two independently-hosted graphs at query time."
        ),
    )
    async def find_by_global_id(
        global_id: str = Field(description="global_id value to look up, e.g. from another graph's bcatlas_get_node output"),
        country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') for a specific (country,"
                " version) pair's graph instead of the default. Must be"
                " paired with `version`."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact `commit_sha` (NOT `version_string`) from"
                " bcatlas_resolve_version/bcatlas_request_version, paired"
                " with `country`."
            ),
        ),
    ) -> Any:
        return await _forward(
            graph_url,
            "bcatlas_find_by_global_id",
            {"global_id": global_id, "country": country, "version": version},
        )

    @mcp.tool(
        name="bcatlas_get_neighbors",
        description=(
            "Get all direct neighbors of a BC object/procedure node with edge"
            " details -- e.g. everything that calls or subscribes to it, and"
            " everything it references."
        ),
    )
    async def get_neighbors(
        label: str = Field(description="Node label or ID to look up"),
        relation_filter: str | None = Field(default=None, description="Optional: filter by relation type"),
        country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') for a specific (country,"
                " version) pair's graph instead of the default. Must be"
                " paired with `version`."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact `commit_sha` (NOT `version_string`) from"
                " bcatlas_resolve_version/bcatlas_request_version, paired"
                " with `country`."
            ),
        ),
    ) -> Any:
        return await _forward(
            graph_url,
            "bcatlas_get_neighbors",
            {"label": label, "relation_filter": relation_filter, "country": country, "version": version},
        )

    @mcp.tool(
        name="bcatlas_get_signature",
        description=(
            "Lightweight ground-truth check: the exact declaration header"
            " (object header, or procedure/trigger signature with its return"
            " type) for a node, re-read from the real w1-28 source -- no"
            " body. Use this to confirm a search/graph hit is the right one"
            " before pulling the full body with bcatlas_get_procedure_body or"
            " bcatlas_get_object_source."
        ),
    )
    async def get_signature(
        label: str = Field(description="Node label or ID to look up"),
        country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') for a specific (country,"
                " version) pair's graph instead of the default. Must be"
                " paired with `version`."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact `commit_sha` (NOT `version_string`) from"
                " bcatlas_resolve_version/bcatlas_request_version, paired"
                " with `country`."
            ),
        ),
    ) -> Any:
        return await _forward(
            graph_url, "bcatlas_get_signature", {"label": label, "country": country, "version": version}
        )

    @mcp.tool(
        name="bcatlas_get_procedure_body",
        description=(
            "Exact, full source text of one procedure/trigger, re-read from"
            " the real w1-28 source (not the index) -- signature, var"
            " declarations, and every line of the body. Use this once"
            " search/graph/bcatlas_get_signature has narrowed down to a specific"
            " procedure and you need to verify its real behavior rather than"
            " guess it. Errors if the node isn't inside a procedure/trigger;"
            " use bcatlas_get_object_source for object-level nodes."
        ),
    )
    async def get_procedure_body(
        label: str = Field(description="Node label or ID to look up"),
        country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') for a specific (country,"
                " version) pair's graph instead of the default. Must be"
                " paired with `version`."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact `commit_sha` (NOT `version_string`) from"
                " bcatlas_resolve_version/bcatlas_request_version, paired"
                " with `country`."
            ),
        ),
    ) -> Any:
        return await _forward(
            graph_url, "bcatlas_get_procedure_body", {"label": label, "country": country, "version": version}
        )

    @mcp.tool(
        name="bcatlas_get_object_source",
        description=(
            "Exact, full source text of the object (table/page/codeunit/...)"
            " a node belongs to, re-read from the real w1-28 source. Pass"
            " either the object's own node or any procedure inside it --"
            " both resolve to the same object source. Can return a lot of"
            " text for large objects; prefer bcatlas_get_procedure_body when you"
            " only need one procedure."
        ),
    )
    async def get_object_source(
        label: str = Field(description="Node label or ID to look up"),
        country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') for a specific (country,"
                " version) pair's graph instead of the default. Must be"
                " paired with `version`."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact `commit_sha` (NOT `version_string`) from"
                " bcatlas_resolve_version/bcatlas_request_version, paired"
                " with `country`."
            ),
        ),
    ) -> Any:
        return await _forward(
            graph_url, "bcatlas_get_object_source", {"label": label, "country": country, "version": version}
        )

    @mcp.tool(name="bcatlas_get_community", description="Get all nodes in a graph community by community ID.")
    async def get_community(
        community_id: int = Field(description="Community ID (0-indexed by size)"),
        country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') for a specific (country,"
                " version) pair's graph instead of the default. Must be"
                " paired with `version`."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact `commit_sha` (NOT `version_string`) from"
                " bcatlas_resolve_version/bcatlas_request_version, paired"
                " with `country`."
            ),
        ),
    ) -> Any:
        return await _forward(
            graph_url, "bcatlas_get_community", {"community_id": community_id, "country": country, "version": version}
        )

    @mcp.tool(
        name="bcatlas_god_nodes",
        description="Return the most connected nodes -- the core abstractions of the base application.",
    )
    async def god_nodes(
        top_n: int = Field(default=10),
        country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') for a specific (country,"
                " version) pair's graph instead of the default. Must be"
                " paired with `version`."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact `commit_sha` (NOT `version_string`) from"
                " bcatlas_resolve_version/bcatlas_request_version, paired"
                " with `country`."
            ),
        ),
    ) -> Any:
        return await _forward(graph_url, "bcatlas_god_nodes", {"top_n": top_n, "country": country, "version": version})

    @mcp.tool(
        name="bcatlas_graph_stats",
        description="Summary statistics for the structural graph: node count, edge count, communities, confidence breakdown.",
    )
    async def graph_stats(country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') for a specific (country,"
                " version) pair's graph instead of the default. Must be"
                " paired with `version`."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact `commit_sha` (NOT `version_string`) from"
                " bcatlas_resolve_version/bcatlas_request_version, paired"
                " with `country`."
            ),
        ),
    ) -> Any:
        return await _forward(graph_url, "bcatlas_graph_stats", {"country": country, "version": version})

    @mcp.tool(
        name="bcatlas_shortest_path",
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
        country: str | None = Field(
            default=None,
            description=(
                "Country code (e.g. 'w1', 'us') for a specific (country,"
                " version) pair's graph instead of the default. Must be"
                " paired with `version`."
            ),
        ),
        version: str | None = Field(
            default=None,
            description=(
                "Exact `commit_sha` (NOT `version_string`) from"
                " bcatlas_resolve_version/bcatlas_request_version, paired"
                " with `country`."
            ),
        ),
    ) -> Any:
        return await _forward(
            graph_url,
            "bcatlas_shortest_path",
            {"source": source, "target": target, "max_hops": max_hops, "country": country, "version": version},
        )

    @mcp.tool(
        name="bcatlas_list_countries",
        description=(
            "List every Business Central country localization available to"
            " query -- a finite, human-usable list, never a raw dump of"
            " hundreds of branch names. Call this first when you don't"
            " already know which country code (e.g. 'w1', 'us', 'de') to use."
        ),
    )
    async def list_countries() -> Any:
        return await _forward(registry_url, "bcatlas_list_countries", {})

    @mcp.tool(
        name="bcatlas_list_versions",
        description=(
            "List the major versions available for one country, summarized"
            " as one entry per major.minor with that minor's latest real"
            " build -- never one entry per raw build commit. Returns a"
            " structured error if the country doesn't exist."
        ),
    )
    async def list_versions(
        country: str = Field(description="Country code, e.g. 'w1', 'us', 'de' (from bcatlas_list_countries)."),
    ) -> Any:
        return await _forward(registry_url, "bcatlas_list_versions", {"country": country})

    @mcp.tool(
        name="bcatlas_resolve_version",
        description=(
            "Resolve a version spec to exactly one unambiguous real build --"
            " accepts an exact identifier (version string or commit sha) or"
            " a loose 'major.minor' spec (e.g. '28.1' = newest build of"
            " major 28, minor 1). NEVER guesses: an unresolvable or"
            " ambiguous spec is rejected explicitly with resolved: false,"
            " never silently mapped to a possibly-wrong version. Call this"
            " before any tool that needs an exact (country, version) pair --"
            " the returned `commit_sha` is what every other tool's `version`"
            " parameter expects, not `version_string`."
        ),
    )
    async def resolve_version(
        country: str = Field(description="Country code, e.g. 'w1', 'us', 'de'."),
        spec: str = Field(
            description="Exact version string, exact commit sha, or loose 'major.minor' spec (e.g. '28.1')."
        ),
    ) -> Any:
        return await _forward(registry_url, "bcatlas_resolve_version", {"country": country, "spec": spec})

    @mcp.tool(
        name="bcatlas_request_version",
        description=(
            "Request that a (country, version) pair's search + graph data"
            " become available, building it from real upstream source if"
            " not already warm. Returns immediately -- status=ready means"
            " usable now with the `commit_sha` returned here as `version`"
            " on any search/graph tool; status=queued/in_progress means a"
            " build was started (or an identical in-flight request was"
            " reused) -- poll bcatlas_version_status for completion. Never"
            " blocks until the build finishes; building a brand new"
            " (country, version) pair can take real time."
        ),
    )
    async def request_version(
        country: str = Field(description="Country/localization code, e.g. 'w1', 'us', 'de'."),
        spec: str = Field(
            description="Exact version string, exact commit sha, or loose 'major.minor' spec (e.g. '28.1')."
        ),
    ) -> Any:
        return await _forward(build_url, "bcatlas_request_version", {"country": country, "spec": spec})

    @mcp.tool(
        name="bcatlas_version_status",
        description=(
            "Poll build status for a (country, commit_sha) pair previously"
            " requested via bcatlas_request_version. state is 'unknown' for"
            " a commit never requested, 'queued'/'in_progress' while"
            " building, 'ready' once search/graph tools can be used against"
            " it, or 'failed' (request it again to retry -- a failed build"
            " is never silently resumed)."
        ),
    )
    async def version_status(
        country: str = Field(description="Country/localization code, e.g. 'w1'."),
        commit_sha: str = Field(description="Exact commit sha returned by bcatlas_request_version."),
    ) -> Any:
        return await _forward(build_url, "bcatlas_version_status", {"country": country, "commit_sha": commit_sha})

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
    async def list_warm_versions(
        country: str | None = Field(
            default=None,
            description="Restrict to one country/localization code, e.g. 'w1'. Omit for every country.",
        ),
    ) -> Any:
        return await _forward(build_url, "bcatlas_list_warm_versions", {"country": country})

    @mcp.tool(
        name="bcatlas_diff",
        description=(
            "Diff a single file or a single object/procedure between two"
            " resolved versions of the SAME country -- never a"
            " whole-repository diff. Scope with EXACTLY ONE of `path`"
            " (file scope) or `object_type`+`object_name` (symbol scope,"
            " optionally narrowed further with `procedure_name`). A"
            " request with neither, or both, is rejected explicitly rather"
            " than silently producing a large or ambiguous result. Symbol"
            " scope independently locates and extracts the named"
            " object/procedure in EACH version (never a raw line diff --"
            " line numbers shift between versions), so `diff_text` only"
            " ever reflects that symbol's own change. `from_found`/"
            " `to_found` report the added/removed-between-versions case"
            " explicitly, never as an error."
        ),
    )
    async def diff(
        country: str = Field(description="Country code, e.g. 'w1', 'us', 'de'."),
        from_spec: str = Field(
            description="Version spec for the 'before' side -- exact build string, commit sha, or loose 'major.minor'."
        ),
        to_spec: str = Field(description="Version spec for the 'after' side -- same spec forms as from_spec."),
        path: str | None = Field(
            default=None,
            description="File scope: exact repository-relative path. Mutually exclusive with object_type/object_name.",
        ),
        object_type: str | None = Field(
            default=None, description="Symbol scope: AL object type, e.g. 'codeunit', 'page', 'pageextension'."
        ),
        object_name: str | None = Field(default=None, description="Symbol scope: AL object name, e.g. 'Sales-Post'."),
        procedure_name: str | None = Field(
            default=None,
            description="Symbol scope, optional: procedure/trigger name. Omit to diff the whole object's text.",
        ),
    ) -> Any:
        return await _forward(
            registry_url,
            "bcatlas_diff",
            {
                "country": country,
                "from_spec": from_spec,
                "to_spec": to_spec,
                "path": path,
                "object_type": object_type,
                "object_name": object_name,
                "procedure_name": procedure_name,
            },
        )

    @mcp.tool(
        name="bcatlas_symbol_history",
        description=(
            "Walk the multi-step change history of a single object/"
            "procedure across a version range of the SAME country --"
            " returns only the real points where that symbol's OWN"
            " resolved text changed, never every commit that merely"
            " touched its containing file. `granularity` controls the"
            " shape: 'endpoints' (default) returns just the start/end"
            " states; 'full' returns every real intermediate change step"
            " too, including a symbol being added, removed, or reverted"
            " within the range."
        ),
    )
    async def symbol_history(
        country: str = Field(description="Country code, e.g. 'w1', 'us', 'de'."),
        from_spec: str = Field(
            description="Version spec for the start of the range -- exact build string, commit sha, or loose 'major.minor'."
        ),
        to_spec: str = Field(description="Version spec for the end of the range -- same spec forms as from_spec."),
        object_type: str = Field(description="AL object type, e.g. 'codeunit', 'page', 'pageextension'."),
        object_name: str = Field(description="AL object name, e.g. 'Sales-Post'."),
        procedure_name: str | None = Field(
            default=None, description="Optional: procedure/trigger name. Omit to track the whole object's history."
        ),
        granularity: str = Field(
            default="endpoints",
            description="'endpoints' (default) for just start/end, or 'full' for every real-change step in between.",
        ),
    ) -> Any:
        return await _forward(
            registry_url,
            "bcatlas_symbol_history",
            {
                "country": country,
                "from_spec": from_spec,
                "to_spec": to_spec,
                "object_type": object_type,
                "object_name": object_name,
                "procedure_name": procedure_name,
                "granularity": granularity,
            },
        )

    return mcp


def main() -> None:
    import os

    # Nothing in this process previously logged a timestamp, making a
    # reported incident (e.g. a client error against the public tunnel
    # endpoint) impossible to correlate with what this server actually did
    # at that moment. This applies to our own `_log` calls in `_forward`,
    # httpx's per-request log lines, and uvicorn's internal error/traceback
    # logger ("uvicorn.error") -- uvicorn's plain startup/access lines keep
    # their own untimestamped handlers regardless (uvicorn.config sets
    # propagate=False on those two loggers specifically), so those are
    # unaffected here.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--search-url", default="http://127.0.0.1:8801/mcp")
    parser.add_argument("--graph-url", default="http://127.0.0.1:8802/mcp")
    parser.add_argument("--registry-url", default="http://127.0.0.1:8803/mcp")
    parser.add_argument("--build-url", default="http://127.0.0.1:8804/mcp")
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

    mcp_server = create_aggregator(args.search_url, args.graph_url, args.registry_url, args.build_url)
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
    print(f"  registry -> {args.registry_url}")
    print(f"  build   -> {args.build_url}")
    if args.public_hostname:
        print(f"  public hostname allow-listed -> {args.public_hostname}")
    asyncio.run(mcp_server.run_streamable_http_async())


if __name__ == "__main__":
    main()
