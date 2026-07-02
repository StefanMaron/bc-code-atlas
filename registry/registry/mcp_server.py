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

from . import diff as diff_module
from . import git_ops
from . import history as history_module
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
    "\n\n"
    "Once you have two resolved versions of the same country,"
    " `bcatlas_diff` diffs one file or one object/procedure between them"
    " (never a whole-repository diff -- an unscoped request is rejected),"
    " and `bcatlas_symbol_history` walks a wider version range for a"
    " single symbol, returning only the real points where that symbol's"
    " own text changed -- never every commit that merely touched its"
    " containing file."
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


def _version_dict(commit_sha: str, version_string: str) -> dict[str, Any]:
    return {"commit_sha": commit_sha, "version_string": version_string}


async def _resolve_spec(
    loop: asyncio.AbstractEventLoop, country: str, spec: str, which: str
) -> tuple[resolver.ResolvedVersion | None, dict[str, Any] | None]:
    """Resolve one `(country, spec)` pair for a tool (`bcatlas_diff`,
    `bcatlas_symbol_history`) that needs TWO resolved versions per call.
    Returns `(ResolvedVersion, None)` on success, or `(None, error_dict)`
    on failure -- `error_dict` reuses `bcatlas_resolve_version`'s
    `{resolved: false, reason, detail}` shape (contracts/registry-tools.md
    "Shared error shape"), with a `which` field ("from_spec"/"to_spec") added
    since a caller needs to know WHICH of the two specs failed to resolve.
    An upstream-unreachable failure raises `resolver.UpstreamUnavailableError`
    -- the caller (each tool below) catches that itself, since it applies
    to the whole request, not to one spec.
    """
    result = await loop.run_in_executor(None, lambda: resolver.resolve_version(country, spec))
    if isinstance(result, resolver.ResolvedVersion):
        return result, None
    return None, {
        "resolved": False,
        "which": which,
        "reason": result.reason,
        "detail": result.detail,
    }


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

    @mcp.tool(
        name="bcatlas_diff",
        description=(
            "Diff a single file or a single object/procedure between two"
            " resolved versions of the SAME country -- never a"
            " whole-repository diff. Scope with EXACTLY ONE of `path`"
            " (file scope) or `object_type`+`object_name` (symbol scope,"
            " optionally narrowed further with `procedure_name` -- omit it"
            " to diff the whole object). A request with neither, or both,"
            " is rejected explicitly rather than silently producing a"
            " large or ambiguous result. Symbol scope is diffed by"
            " independently locating and extracting the named"
            " object/procedure in EACH version (never a raw line diff --"
            " line numbers shift between versions), so `diff_text` only"
            " ever reflects that symbol's own change. `from_found`/"
            " `to_found` report the added/removed-between-versions case"
            " explicitly (e.g. a procedure that didn't exist yet, or was"
            " later deleted) -- this is never treated as an error. Resolve"
            " `from_spec`/`to_spec` first with `bcatlas_resolve_version` if"
            " you don't already have exact identifiers."
        ),
    )
    async def diff(
        country: str = Field(description="Country code, e.g. 'w1', 'us', 'de'."),
        from_spec: str = Field(
            description="Version spec for the 'before' side -- exact build string, commit sha, or loose 'major.minor'."
        ),
        to_spec: str = Field(
            description="Version spec for the 'after' side -- same spec forms as from_spec."
        ),
        path: str | None = Field(
            default=None,
            description=(
                "File scope: exact repository-relative path, e.g."
                " 'Base Application/.../Foo.Codeunit.al'. Mutually"
                " exclusive with object_type/object_name."
            ),
        ),
        object_type: str | None = Field(
            default=None,
            description="Symbol scope: AL object type, e.g. 'codeunit', 'page', 'pageextension'.",
        ),
        object_name: str | None = Field(
            default=None,
            description="Symbol scope: AL object name, e.g. 'Sales-Post'.",
        ),
        procedure_name: str | None = Field(
            default=None,
            description=(
                "Symbol scope, optional: procedure/trigger name within the"
                " object. Omit to diff the whole object's text instead of"
                " one procedure."
            ),
        ),
    ) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        try:
            from_result, from_error = await _resolve_spec(loop, country, from_spec, "from_spec")
            if from_error is not None:
                return from_error
            to_result, to_error = await _resolve_spec(loop, country, to_spec, "to_spec")
            if to_error is not None:
                return to_error
        except resolver.UpstreamUnavailableError as e:
            return _upstream_unavailable(str(e))

        try:
            result = await loop.run_in_executor(
                None,
                lambda: diff_module.diff(
                    country,
                    from_result.commit_sha,
                    from_result.version_string,
                    to_result.commit_sha,
                    to_result.version_string,
                    path=path,
                    object_type=object_type,
                    object_name=object_name,
                    procedure_name=procedure_name,
                ),
            )
        except diff_module.DiffScopeError as e:
            return {"error": "unscoped_diff_rejected", "detail": str(e)}
        except git_ops.GitOpsError as e:
            return _upstream_unavailable(str(e))

        return {
            "scope": result.scope,
            "country": result.country,
            "from_version": _version_dict(result.from_commit_sha, result.from_version_string),
            "to_version": _version_dict(result.to_commit_sha, result.to_version_string),
            "path": result.path,
            "symbol": (
                {
                    "object_type": result.symbol.object_type,
                    "object_name": result.symbol.object_name,
                    "procedure_name": result.symbol.procedure_name,
                }
                if result.symbol is not None
                else None
            ),
            "diff_text": result.diff_text,
            "from_found": result.from_found,
            "to_found": result.to_found,
        }

    @mcp.tool(
        name="bcatlas_symbol_history",
        description=(
            "Walk the multi-step change history of a single object/"
            "procedure across a version range of the SAME country --"
            " returns only the real points where that symbol's OWN"
            " resolved text changed, never every commit that merely"
            " touched its containing file (a common case: shared files get"
            " touched by unrelated changes constantly). `granularity`"
            " controls the shape: 'endpoints' (default) returns exactly"
            " the start and end states (useful to quickly confirm 'did"
            " this change at all across this range'); 'full' returns every"
            " real intermediate change step too, including a symbol being"
            " added, removed, or reverted within the range. Resolve"
            " `from_spec`/`to_spec` first with `bcatlas_resolve_version` if"
            " you don't already have exact identifiers."
        ),
    )
    async def symbol_history(
        country: str = Field(description="Country code, e.g. 'w1', 'us', 'de'."),
        from_spec: str = Field(
            description="Version spec for the start of the range -- exact build string, commit sha, or loose 'major.minor'."
        ),
        to_spec: str = Field(
            description="Version spec for the end of the range -- same spec forms as from_spec."
        ),
        object_type: str = Field(description="AL object type, e.g. 'codeunit', 'page', 'pageextension'."),
        object_name: str = Field(description="AL object name, e.g. 'Sales-Post'."),
        procedure_name: str | None = Field(
            default=None,
            description="Optional: procedure/trigger name within the object. Omit to track the whole object's history.",
        ),
        granularity: str = Field(
            default="endpoints",
            description="'endpoints' (default) for just the start/end states, or 'full' for every real-change step in between.",
        ),
    ) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        try:
            from_result, from_error = await _resolve_spec(loop, country, from_spec, "from_spec")
            if from_error is not None:
                return from_error
            to_result, to_error = await _resolve_spec(loop, country, to_spec, "to_spec")
            if to_error is not None:
                return to_error
        except resolver.UpstreamUnavailableError as e:
            return _upstream_unavailable(str(e))

        try:
            result = await loop.run_in_executor(
                None,
                lambda: history_module.build_history(
                    country,
                    from_result.commit_sha,
                    from_result.version_string,
                    to_result.commit_sha,
                    to_result.version_string,
                    object_type,
                    object_name,
                    procedure_name,
                    granularity=granularity,
                ),
            )
        except history_module.SymbolNotLocatedError as e:
            return {"error": "symbol_not_found", "detail": str(e)}
        except ValueError as e:
            return {"error": "invalid_request", "detail": str(e)}
        except git_ops.GitOpsError as e:
            return _upstream_unavailable(str(e))

        return {
            "symbol": {
                "object_type": result.symbol.object_type,
                "object_name": result.symbol.object_name,
                "procedure_name": result.symbol.procedure_name,
            },
            "country": result.country,
            "from_version": _version_dict(result.from_commit_sha, result.from_version_string),
            "to_version": _version_dict(result.to_commit_sha, result.to_version_string),
            "granularity": result.granularity,
            "steps": [
                {
                    "commit_sha": step.commit_sha,
                    "version_string": step.version_string,
                    "text": step.text,
                    "found": step.found,
                    "changed_from_previous": step.changed_from_previous,
                }
                for step in result.steps
            ],
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
