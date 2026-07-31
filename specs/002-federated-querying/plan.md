# Implementation Plan: Federated Multi-Instance Querying

**Branch**: `002-federated-querying` | **Date**: 2026-07-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-federated-querying/spec.md`

## Summary

Let a private, self-hosted bc-code-atlas deployment fill the structural/search
gaps in its own small corpus by querying outward to one or more trusted remote
bc-code-atlas instances (starting with the public one), joining on the
`global_id` graphify-al already stamps on every node. The federation layer
lives entirely in the **aggregator** (`aggregator/unified_mcp_server.py`),
which already owns exactly this role for its own four backends: it is a thin
MCP-over-HTTP forwarder with no state of its own. Federation adds a fifth kind
of outbound target -- zero or more *remote aggregators* -- and two behaviors:
(1) when a graph tool's response contains an unresolved external stub, try to
resolve it against configured remote endpoints by `global_id`; (2) when
`bcatlas_search` runs, optionally fan the same query out to remote endpoints
and merge the labeled results. No build/serve infrastructure changes; no
graphify-al *extraction* changes. One necessary, minimal serving-layer
addition is required (see Research R2) to make (1) possible at all.

## Technical Context

**Language/Version**: Python 3.13 (matches `aggregator/`'s existing stack)

**Primary Dependencies**: FastMCP (existing aggregator framework), `httpx`
(existing `_forward` HTTP client), MCP Python SDK (existing graph/search
backends)

**Storage**: N/A -- the aggregator is stateless; federation endpoint
configuration is process config (env/CLI), not a database

**Testing**: pytest, following the existing `aggregator/tests/` pattern
(in-process FastMCP test client against a live HTTP backend -- Principle I),
plus a lightweight fake remote-aggregator fixture for federation-specific
tests

**Target Platform**: Linux server, same deployment shape as the existing
aggregator (Principle I: MCP over HTTP, no stdio-only shortcut)

**Project Type**: Extension of an existing web-service (MCP aggregator) --
no new top-level project

**Performance Goals**: A federated stub resolution or search fan-out adds at
most one bounded remote round-trip per request (Principle applied from
FR-011's one-hop rule); default remote-call timeout keeps SC-004's "local
results within the same time budget as non-federated + one bounded delay"
provable, not just asserted (Principle V -- see Research R4)

**Constraints**: No private-corpus source/file/body data crosses the
boundary (FR-005); local real nodes always outrank remote (FR-009); at most
one federation hop (FR-011); remote endpoint credentials MUST be supplied via
environment variable injection (this operator's own standing secrets
convention: 1Password `claude` vault via `op run`, never a literal secret in
a config file) -- consistent with, not weaker than, existing aggregator
practice for its own backend URLs

**Scale/Scope**: A small, operator-configured list of remote endpoints (not
a discovery/marketplace service, per the spec's explicit non-goal) --
typically one (the public instance) to low single digits

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Serve Like It's Remote** -- PASS. Federation is inherently a
  network call to a peer's own MCP-over-HTTP surface; it reuses the exact
  transport the aggregator already uses for its four existing backends. No
  in-process shortcut is introduced.
- **II. Build and Serve Are Separate Resource Pools** -- PASS
  (not applicable). Federation is a pure serve-time concern; it never
  triggers, reads, or writes build/staging state. A private instance's own
  build queue is untouched.
- **III. Historical Versions Are Immutable — Only Tips Move** -- PASS.
  Federated results carry the remote instance's resolved (country, version)
  per FR-012, so immutability/version identity is preserved across the
  boundary rather than laundered away.
- **IV. Unbounded Scope, Bounded Residency** -- PASS. Federation endpoints
  are an operator-configured, bounded list (spec non-goal: no
  discovery/marketplace); this does not expand what a given instance MUST
  be able to build itself, it only adds an additional query path to
  *other* instances' already-served data.
- **V. Measure, Don't Assume** -- CONDITIONAL, tracked in Research R4.
  SC-004's latency claim ("local results within the same time budget... plus
  no more than one bounded remote-timeout delay") must be validated against
  a real fake-remote-endpoint timing run before being asserted as met, not
  argued from design alone.
- **VI. Minimal, Justified Forks** -- PASS, with one flagged exception
  (Research R2): the feature composes existing primitives (the aggregator's
  `_forward` pattern, the existing per-tool `country`/`version` routing) for
  everything except one new minimal serving-layer tool needed to look up a
  node by `global_id`, which does not exist today in any backend. This is a
  serving-layer *addition* (a new MCP tool in `graphify-al/graphify/serve.py`,
  the T029 multi-tenant serving code this fork already owns and maintains),
  not a fork of graphify-al's extraction logic -- the feature's non-goal of
  "no changes to graphify-al" is read as "no changes to the `global_id`
  extraction mechanism," which stays untouched. Flagged explicitly for the
  user before implementation proceeds, since it revises what "out of scope"
  meant in the original request.
- **VII. Lean, Honest Agent-Facing Output** -- PASS, with design
  obligation. Federated attribution (FR-003) must ride on existing response
  fields rather than adding verbose new structure; tool descriptions for any
  federation-aware tool must state plainly when federation is configured/
  active so a calling agent isn't surprised by cross-instance data mixed
  into a response it expected to be purely local.

No unjustified violations. One scope note (R2) requires explicit user
sign-off before implementation; tracked in Complexity Tracking below rather
than silently proceeding.

## Project Structure

### Documentation (this feature)

```text
specs/002-federated-querying/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md         # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
aggregator/
├── unified_mcp_server.py   # existing: create_aggregator(), _forward(), tool defs
├── federation.py           # NEW: remote endpoint config, per-tool federation
│                            #      fan-out/merge helpers, stub detection
├── tests/
│   ├── test_federation.py  # NEW: federation-specific behavior
│   └── fixtures/
│       └── fake_remote_aggregator.py  # NEW: minimal fake MCP peer for tests

tools/graphify-al/
└── graphify/
    └── serve.py             # ONE NEW TOOL: bcatlas_resolve_global_id
                              # (see Constitution Check VI / Research R2)
```

**Structure Decision**: Federation logic lives inside the existing
`aggregator/` project as a new `federation.py` module invoked from
`unified_mcp_server.py`'s existing tool handlers -- no new top-level service,
consistent with "prefer composing existing, verified primitives" (Development
Workflow) and the aggregator's existing sole responsibility (forwarding/
merging MCP calls across backends). The one addition outside `aggregator/` is
a single new lookup tool on the graph server (`graphify-al/graphify/serve.py`)
because no existing tool can answer "give me the real node for this
`global_id`" -- required for User Story 1, not optional.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|---------------------------------------|
| New MCP tool `bcatlas_resolve_global_id` added to `graphify-al/graphify/serve.py`, touching the fork the original feature request said was out of scope | User Story 1 (the feature's core P1 value) requires resolving a stub's `global_id` against a remote graph; no existing tool on any backend accepts `global_id` as a lookup key -- `bcatlas_get_node`/`bcatlas_get_signature` etc. all key on `label`, which a stub's own private-side label doesn't reliably match the remote's real object name | Doing the lookup by re-deriving a `label` guess client-side was rejected -- `global_id`'s whole purpose (per graphify-al commit 4e98856) is to be the deterministic join key precisely *because* labels/qualifiers can differ; bypassing it would reintroduce the ambiguity it was built to remove. This is scoped to serve.py's existing T029 multi-tenant tool surface, not the extraction logic in extract.py the original non-goal was actually protecting. |
