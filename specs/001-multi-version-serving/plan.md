# Implementation Plan: Multi-Country, Multi-Version Serving

**Branch**: `001-multi-version-serving` | **Date**: 2026-07-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-multi-version-serving/spec.md`

## Summary

Replace the hardcoded single-version (`w1-28`) local setup with three new capabilities,
matching the spec's three user stories: (1) a registry that discovers countries/versions and
resolves specs to exact commits, (2) file/symbol-scoped diffing plus multi-step symbol
change-history, and (3) on-demand building and serving of any (country, version) pair via a
build/serve split — bounded GPU build queue writing to staging + atomic promote, multi-tenant
CPU-served reads, clone-then-patch incremental builds, LRU/TTL eviction. All reachable as MCP
tools over HTTP through the existing aggregator, per constitution Principle I.

## Technical Context

**Language/Version**: Python 3.13 (matches `chunker/`, `aggregator/`, `tools/graphify-al`)

**Primary Dependencies**: `mcp` SDK / FastMCP (existing pattern), `tree-sitter` +
`tree-sitter-al` (existing, via `tools/graphify-al`'s `_AL_CONFIG`), `cocoindex-code`
(existing, unforked, via its stock `ccc index`/daemon client), `git` CLI (subprocess, no new
Python git library — git plumbing commands are simple and already proven this session via
direct shell use)

**Storage**: git itself (source of truth for versions/content — no new database); on-disk
warm-artifact directories per (country, version) holding cocoindex-code's existing two
SQLite files plus graphify-al's `graph.json`; no new persistent database introduced

**Testing**: unit/integration tests for pure-logic pieces (version spec resolution, symbol-
span extraction/comparison, eviction policy decisions); live end-to-end verification via a
separate MCP client session against real HTTP servers for the full flow, per constitution
Principle I — this is the primary acceptance mechanism, not a substitute for it

**Target Platform**: Linux server (self-hosted, matches current deployment model)

**Project Type**: Multi-service backend (existing pattern: independent `uv`-managed Python
projects per concern, connected only via MCP-over-HTTP, never in-process imports across
service boundaries except where already established, e.g. registry importing graphify-al's
tree-sitter config as a local path dependency)

**Performance Goals**: query-serving reads stay in the tens-of-ms range already measured for
the existing single-version setup (constitution's "Technology & Data Constraints" — query
embedding ~8-10ms regardless of GPU/CPU); a same-country version-hop build completes in
substantially less time than a cold build (target: proportional to the ~1% file-change
figure already measured, not a fixed number — validate empirically per constitution
Principle V rather than asserting a number here)

**Constraints**: constitution Principles I-VII bind this plan directly — see Constitution
Check below; no country/version may be hardcoded anywhere in new code; a build-writing
process and a serve-reading process must never hold concurrent open connections to the same
on-disk artifact (cocoindex-code's SQLite has no WAL, confirmed by direct source inspection
this session)

**Scale/Scope**: ~51 countries × ~10-11 major versions each in the real upstream repository;
warm residency is configuration-bounded (disk budget), not a fixed count; this feature does
not need to pre-build or pre-index anything — everything is built on first real request

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design below.*

| Principle | Gate | Status |
|---|---|---|
| I. Serve Like It's Remote | All new capabilities are MCP tools over HTTP through the aggregator; no in-process shortcut planned. | PASS |
| II. Build/Serve Separation | Build writes to staging, promotion is atomic move/rename, serving only opens promoted read-only artifacts; bounded GPU-aware build queue; one shared embedding-capable serving process rather than per-version model loads. | PASS (design commits to this explicitly — see data-model.md `Build` entity and contracts) |
| III. Immutable Versions, Live Tips Only | Registry treats every resolved exact commit as immutable; only "latest within a spec" resolution re-queries upstream on each call, resolved exact versions are never re-resolved differently later. | PASS |
| IV. Unbounded Scope, Bounded Residency | No country/version hardcoded; LRU/TTL eviction is config-driven, not a fixed cap on distinct servable pairs. | PASS |
| V. Measure, Don't Assume | Plan reuses only already-measured numbers (269-file/~1% same-country delta, ~87% cross-country content overlap) and defers any new cost claim (incremental build wall-clock, disk-per-version) to actual measurement during implementation, not asserted here. | PASS |
| VI. Minimal, Justified Forks | cocoindex-code stays unforked (stock `ccc index` against a cloned+patched directory); graphify-al (already forked, justified) gains new capability (symbol-by-name lookup, multi-tenant serving) as an extension of the existing fork, documented per change. | PASS |
| VII. Lean, Honest Agent-Facing Output | New tool descriptions must name real behavior (e.g. build latency, scope requirements) accurately; diff/history tools refuse unscoped requests rather than silently truncating. | PASS (enforced via FR-007/FR-005 in spec, carried into contracts) |

No violations requiring Complexity Tracking justification.

## Project Structure

### Documentation (this feature)

```text
specs/001-multi-version-serving/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md         # Phase 1 output
├── contracts/            # Phase 1 output — MCP tool contracts
└── tasks.md              # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
registry/                        # NEW — uv-managed Python project
├── pyproject.toml               # path-depends on tools/graphify-al for tree-sitter-al config reuse
├── registry/
│   ├── git_ops.py               # ls-remote, scoped fetch-by-sha, git show (blob), git log --oneline -- <path>
│   ├── resolver.py              # version-spec parsing + resolution (exact / "latest within X")
│   ├── symbols.py                # find a named object/procedure span in an arbitrary blob (extends
│   │                              #   graphify-al's source_lookup pattern to lookup-by-name instead of
│   │                              #   lookup-by-line, since diff/history targets have no graph node yet)
│   ├── diff.py                   # file-scoped (git diff) and symbol-scoped (blob-fetch + symbols.py + text diff)
│   ├── history.py                # multi-step symbol change-history chain
│   └── mcp_server.py             # MCP HTTP server: bcatlas_list_countries, bcatlas_list_versions,
│                                  #   bcatlas_resolve_version, bcatlas_diff, bcatlas_symbol_history
└── tests/

build/                            # NEW — uv-managed Python project
├── pyproject.toml
├── build/
│   ├── layout.py                 # warm-directory naming convention: data/warm/<country>/<version>/{search,graph}
│   ├── queue.py                  # bounded-concurrency build queue + in-flight request coalescing
│   ├── incremental.py            # clone-nearest-warm-sibling + patch-git-diffed-files + stock `ccc index`
│   ├── promote.py                # staging dir + atomic rename promotion
│   ├── eviction.py                # LRU/TTL sweep under a configured disk budget
│   └── mcp_server.py              # MCP HTTP server: bcatlas_request_version, bcatlas_version_status
└── tests/

chunker/mcp_http_server.py        # MODIFIED — becomes multi-tenant: accepts a resolved warm-directory
                                   #   path per search call instead of one project_root bound at startup;
                                   #   one shared embedding-capable process, LRU pool of open SQLite handles

tools/graphify-al/graphify/       # MODIFIED (submodule, existing fork) — becomes multi-tenant the same way;
serve.py, source_lookup.py        #   source_lookup.py gains a lookup-by-blob-and-name path for registry's
                                   #   diff/history use (find_symbol_by_name), reused rather than duplicated

aggregator/unified_mcp_server.py  # MODIFIED — new proxy tools for registry/build servers; existing search/
                                   #   graph proxy tools gain a resolved-version routing parameter
```

**Structure Decision**: two new independent `uv`-managed services (`registry/`, `build/`),
following the repo's existing pattern of one process per concern connected only via MCP-over-
HTTP (constitution Principle I) — not folded into `chunker/` or `tools/graphify-al` since
their resource profiles and lifecycles differ (registry is stateless/cheap, build is GPU-
bound/queued). `chunker/` and `graphify-al/serve.py` are modified in place (multi-tenant)
rather than duplicated, since the single-tenant behavior they have today is a strict subset
of what's needed — the existing w1-28 setup keeps working as "one warm (country, version)
among possibly several," not a special case.

## Complexity Tracking

*No Constitution Check violations — table not needed.*
