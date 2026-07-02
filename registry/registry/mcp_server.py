"""MCP-over-HTTP server for version discovery/resolution.

Exposes exactly the three tools specced by
`specs/001-multi-version-serving/contracts/registry-tools.md`:
`bcatlas_list_countries`, `bcatlas_list_versions`, `bcatlas_resolve_version`.
Streamable HTTP transport (not stdio), same pattern as
`chunker/mcp_http_server.py` -- every capability MUST be reachable the same
way a remote community user will reach it (constitution Principle I), so
this runs as its own standalone HTTP MCP server rather than an in-process
import, even locally.

This server only wraps `resolver.py` -- no business logic lives here. Tool
outputs are plain dicts matching contracts/registry-tools.md's exact field
shapes (rather than a shared pydantic model per tool), since success and
failure responses for the same tool intentionally have different key sets
(e.g. `bcatlas_resolve_version`'s `{resolved: true, commit_sha, ...}` vs.
`{resolved: false, reason, detail}`) -- a single model would either force
extra always-present-but-often-null fields (Principle VII: no incidental
bloat) or need discriminated-union machinery this three-tool server doesn't
otherwise need.

Aggregator wiring (proxying these through `aggregator/unified_mcp_server.py`)
is explicitly OUT OF SCOPE for this module -- see tasks.md T015, done
separately.

Usage:
    uv run python -m registry.mcp_server --host 127.0.0.1 --port 8803
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import resolver

_MCP_INSTRUCTIONS = (
    "Version discovery and resolution for Microsoft Dynamics 365 Business"
    " Central's AL source, across every country localization and every"
    " shipped version -- not just one hardcoded (country, version) pair."
    "\n\n"
    "Source of truth: the real"
    " StefanMaron/MSDyn365BC.Sandbox.Code.History repository (one branch"
    " per country's line of one major version, one commit per real build;"
    " the commit message IS the exact version string, e.g."
    " 'w1-28.2.50931.52151')."
    "\n\n"
    "Use this BEFORE any other bc-code-atlas tool when you don't already"
    " have an exact, confirmed version identifier for the country/version"
    " you care about: `bcatlas_list_countries` to see what countries exist,"
    " `bcatlas_list_versions` to see what major versions exist for one"
    " country, then `bcatlas_resolve_version` to turn either an exact build"
    " string or a loose spec (e.g. '28.1', meaning 'latest build of major"
    " 28 minor 1') into a single unambiguous commit. Resolution never"
    " guesses -- an ambiguous or unrecognized spec is rejected explicitly,"
    " never silently mapped to the wrong version."
)


def _display_name(code: str) -> str:
    """Human-usable label for a country code (FR-001) -- derived, not
    stored upstream (no curated country-name catalog exists; the code
    itself, uppercased, is already what BC developers recognize, e.g. 'W1'
    for the worldwide base app, 'US', 'DE').
    """
    return code.upper()


def _upstream_unavailable(detail: str) -> dict[str, Any]:
    """Shared error shape (contracts/registry-tools.md "Shared error
    shape") for a real upstream-unreachable failure -- distinct from a
    resolvable-but-not-found/ambiguous spec, which is never an error.
    """
    return {"error": "upstream_unavailable", "detail": detail}


def create_registry_mcp_server() -> FastMCP:
    mcp = FastMCP("bc-code-atlas-registry", instructions=_MCP_INSTRUCTIONS)

    @mcp.tool(
        name="bcatlas_list_countries",
        description=(
            "List every Business Central country localization available to"
            " query -- a finite, human-usable list derived from the real"
            " upstream repository's branch names, never a raw dump of"
            " hundreds of individual branch/version entries. Call this"
            " first when you don't already know which country code (e.g."
            " 'w1', 'us', 'de') to use with `bcatlas_list_versions` or"
            " `bcatlas_resolve_version`."
        ),
    )
    async def list_countries() -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        try:
            codes = await loop.run_in_executor(None, resolver.list_countries)
        except resolver.UpstreamUnavailableError as e:
            return _upstream_unavailable(str(e))
        return {
            "countries": [
                {"code": code, "display_name": _display_name(code)} for code in codes
            ]
        }

    @mcp.tool(
        name="bcatlas_list_versions",
        description=(
            "List the major versions available for one Business Central"
            " country, summarized as one entry per major.minor (e.g."
            " '28.2') with that minor version's latest real build -- never"
            " one entry per raw build commit (there can be dozens per"
            " minor version). Use this after `bcatlas_list_countries` to"
            " see what's available for a chosen country before calling"
            " `bcatlas_resolve_version`. Returns a structured error (not an"
            " empty list) if the country doesn't exist."
        ),
    )
    async def list_versions(
        country: str = Field(
            description="Country code, e.g. 'w1', 'us', 'de' (from bcatlas_list_countries)."
        ),
    ) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        try:
            major_versions = await loop.run_in_executor(
                None, lambda: resolver.list_major_versions(country)
            )
        except resolver.UpstreamUnavailableError as e:
            return _upstream_unavailable(str(e))
        if major_versions is None:
            return {
                "error": "not_found",
                "detail": f"Unknown country: {country!r}.",
            }
        return {"country": country, "major_versions": major_versions}

    @mcp.tool(
        name="bcatlas_resolve_version",
        description=(
            "Resolve a version spec to exactly one unambiguous, real build"
            " -- accepts either an exact identifier (a full build/version"
            " string like 'w1-28.2.50931.52151', or a full 40-character"
            " commit sha) or a loose 'major.minor' spec (e.g. '28.1',"
            " meaning 'the newest real build of major 28, minor 1'). NEVER"
            " guesses: a spec that matches zero builds, or that's too loose"
            " to pick exactly one (e.g. a bare major version like '28'"
            " matching several minor versions), is rejected explicitly with"
            " `resolved: false` and a reason -- it is NEVER silently mapped"
            " to a possibly-wrong version. Call this before any tool that"
            " needs an exact (country, version) pair."
        ),
    )
    async def resolve_version(
        country: str = Field(description="Country code, e.g. 'w1', 'us', 'de'."),
        spec: str = Field(
            description=(
                "Exact version string (e.g. 'w1-28.2.50931.52151'), exact"
                " commit sha, or loose 'major.minor' spec (e.g. '28.1')."
            )
        ),
    ) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None, lambda: resolver.resolve_version(country, spec)
            )
        except resolver.UpstreamUnavailableError as e:
            return _upstream_unavailable(str(e))
        if isinstance(result, resolver.ResolvedVersion):
            return {
                "resolved": True,
                "country": result.country,
                "commit_sha": result.commit_sha,
                "version_string": result.version_string,
            }
        return {
            "resolved": False,
            "reason": result.reason,
            "detail": result.detail,
        }

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    # 8800/8801/8802 are already taken by the aggregator/search/graph
    # servers (see scripts/start-*.sh) -- this is the next free port.
    parser.add_argument("--port", type=int, default=8803)
    args = parser.parse_args()

    mcp_server = create_registry_mcp_server()
    mcp_server.settings.host = args.host
    mcp_server.settings.port = args.port
    print(
        f"bc-code-atlas registry MCP server (streamable-http) on "
        f"http://{args.host}:{args.port}/mcp"
    )
    asyncio.run(mcp_server.run_streamable_http_async())


if __name__ == "__main__":
    main()
