# Phase 0 Research: Federated Multi-Instance Querying

## R1: How remote federation endpoints are configured

**Decision**: Reuse the aggregator's existing startup-configuration pattern.
Today `create_aggregator(search_url, graph_url, registry_url, build_url)` in
`aggregator/unified_mcp_server.py` takes its four backend URLs from
CLI flags/env vars resolved in `main()`. Federation adds a fifth, *repeatable*
input: zero or more `(url, credential_env_var_name)` pairs, supplied as a
`--federation-endpoint URL[|CREDENTIAL_ENV_VAR]` CLI flag (repeatable) and/or
a `BCATLAS_FEDERATION_ENDPOINTS` env var (newline- or comma-separated list of
the same `URL[|CREDENTIAL_ENV_VAR]` form). Order in the list is the
precedence order for FR-010's tie-break.

**Rationale**: Matches existing operational conventions exactly (no new
config format to learn), and keeps credentials out of any config file --
only an env var *name* is stored in config; the actual secret value is
injected the same way this operator already injects every other secret (1Password
`claude` vault via `op run` into a `.op.env` file), never written to disk in
cleartext. This satisfies both FR-005's spirit (nothing private leaves the
boundary) and the project's own standing secrets-handling rule, without
inventing a parallel mechanism.

**Alternatives considered**: A dedicated federation config file (YAML/JSON)
was considered for readability with many endpoints, but rejected for v1 --
the expected scale (Technical Context: "one to low single digits") doesn't
need it, and it would be a second config surface alongside the existing
CLI/env one for no real benefit yet. Can be added later without breaking the
CLI/env form if the operator population grows past a handful of endpoints.

## R2: How stub resolution actually reaches a remote instance's real node

**Finding**: No existing MCP tool on any backend accepts `global_id` as a
lookup key. `bcatlas_get_node`, `bcatlas_get_signature`,
`bcatlas_get_procedure_body`, and `bcatlas_get_object_source` all key on
`label` (the object/procedure name). A stub node in the *private* corpus's
graph does carry the correct `global_id` (graphify-al stamps it via
`ensure_external` at `extract.py:6217-6231`), but there's no tool to hand
that `global_id` to a remote instance and get the real node back.

**Decision**: Add exactly one new MCP tool, `bcatlas_resolve_global_id`, to
`graphify-al/graphify/serve.py` (the existing T029 multi-tenant serving
layer this fork already owns), accepting `global_id` (and the existing
optional `country`/`version` routing args) and returning the same node shape
`bcatlas_get_node` returns when a match exists, or a clear not-found result
otherwise. This is a serve-layer addition only -- `extract.py`'s extraction
logic (where the `global_id` value itself is computed) is untouched.

**Rationale**: This is the single missing primitive standing between "the
join key exists" and "federation can use it." Every other piece of User
Story 1 (detecting a stub in a response, calling out to a configured remote,
merging the result, labeling it) can be built entirely in the aggregator
with zero further graphify-al changes.

**Alternatives considered**: (a) Do the lookup by `label` alone, guessing the
remote's likely name for the object -- rejected, this is exactly the
ambiguity `global_id` was introduced to eliminate (see graphify-al commit
4e98856's rationale: a table and a same-named page must never collide, and a
qualifier/namespace can differ from a bare label). (b) Build a small,
separate federation-only lookup service outside graphify-al that scans a
remote's `graph.json` directly -- rejected, it would bypass the routed/
multi-tenant graph loading `_load_routed_graph` already handles correctly
(caching, LRU-safe access to warm (country, version) pairs), duplicating
logic instead of reusing it, and would require direct filesystem access
across a network boundary that MCP-over-HTTP is specifically meant to avoid
(Principle I).

**Flag for user**: This touches `graphify-al`, which the original feature
request marked out of scope ("no changes to graphify-al itself"). Read
narrowly, that non-goal protected the `global_id` *extraction* mechanism
(just merged from Christian's `al-support` branch) from further local
patching -- not the T029 serving-layer tool surface, which this fork already
extends routinely (it's exactly how `bcatlas_get_signature` /
`bcatlas_get_procedure_body` / multi-tenant routing were added). Proceeding
under that reading, but calling it out explicitly rather than silently
expanding scope.

## R3: Precedence and dedup rules

**Decision**: Implement as pure logic in `aggregator/federation.py`, no
network/storage implications:
1. If the local corpus's own response for a `global_id` is a real (non-stub)
   node, never attempt federation for it (FR-009).
2. Otherwise, try configured remote endpoints in configured list order;
   first real-node match wins (FR-010) -- no scoring/ranking across remotes.
3. Federation never recurses -- the request to a remote's
   `bcatlas_resolve_global_id` never itself carries or honors any
   "also federate further" instruction (FR-011); the new tool from R2 simply
   has no federation parameter at all, which makes the one-hop bound
   structural rather than merely a documented rule to remember.

**Rationale**: Deterministic, cheap, and matches the spec's edge-case
requirements directly without needing any new stored state.

**Alternatives considered**: Score/merge multiple remotes' answers for the
same `global_id` (e.g. prefer the most-recently-built) -- rejected as
unnecessary complexity for the stated scale (a handful of endpoints,
typically one), and the spec explicitly only requires a *deterministic*
tie-break, not an optimal one.

## R4: Latency budget validation for SC-004

**Decision**: Before implementation is considered complete, run a real
timed test (not an estimate) with a fake remote aggregator fixture that
sleeps past a configured timeout, confirming: (a) a federated request whose
remote endpoint hangs still returns local-only results within
local-query-time + one bounded timeout, and (b) the timeout value itself is
a config default (e.g. a small number of seconds, exact value to be picked
during implementation against the fake fixture's measured behavior) with no
retry-storm or unbounded backoff hidden in the HTTP client defaults.

**Rationale**: Constitution Principle V forbids asserting a
cost/feasibility claim without a direct, reproducible measurement --
SC-004 is exactly such a claim. `httpx`'s default timeout behavior must be
explicitly checked, not assumed, since an unconfigured client can hang far
longer than expected.

**Alternatives considered**: None -- this is a measurement task, not a
design choice; deferring it to a "trust the framework defaults" assumption
was rejected outright given Principle V's explicit language about proxy
metrics and unverified assumptions.

## R5: Search fan-out mechanism

**Decision**: `bcatlas_search`'s existing per-tool handler in
`unified_mcp_server.py` gains a call into `federation.py`'s
`fan_out_search(...)` helper when federation endpoints are configured,
issuing the same `bcatlas_search` call (same query/limit/offset/filters --
the user's own literal search terms, satisfying FR-005) against each
configured remote's aggregator, in parallel, bounded by the same timeout
policy as R4. Results are grouped by source (local first, then each remote
in configured order) rather than interleaved into one relevance-ranked list,
per the spec's documented Assumption on search-ranking scope.

**Rationale**: Reuses the exact tool contract remote instances already
expose publicly (`bcatlas_search` on their own aggregator) -- no new remote-
side tool needed for search federation, unlike R2's graph case.

**Alternatives considered**: A dedicated `bcatlas_federated_search`
wrapper tool distinct from `bcatlas_search` was considered, but rejected --
it would fork the tool surface a calling agent already knows, when the
existing tool's own `country`/`version`-free default-corpus behavior already
composes cleanly with "also ask this array of other default corpora."
