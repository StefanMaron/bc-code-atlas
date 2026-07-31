# Feature Specification: Federated Multi-Instance Querying

**Feature Branch**: `002-federated-querying`

**Created**: 2026-07-31

**Status**: Draft

**Input**: User description: "Federated multi-instance querying: allow a private, self-hosted bc-code-atlas deployment (e.g. run internally by another company for their own AL apps, not publicly exposed) to transparently combine its own private graph/search results with results from one or more remote bc-code-atlas instances (starting with our public instance serving the Microsoft base app / W1 and country localizations), without re-hosting or re-indexing the corpora the remote instance already serves. Background: graphify-al now stamps every AL node with a stable global_id so independently-built per-app graphs can be joined deterministically -- an external stub for an object outside a corpus already carries the same global_id as the real node in whichever corpus built it. Private operator's own data must never be exposed publicly or sent to the remote instance -- federation is strictly the private instance querying outward to public/remote instances it trusts, never the reverse. Not in scope: changes to graphify-al itself, or a marketplace/discovery layer for arbitrary third-party instances."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Cross-boundary structural edges resolve to real objects, not dead stubs (Priority: P1)

A developer at a company that self-hosts a private bc-code-atlas instance for their own internal AL apps asks their coding agent a structural question that crosses into Microsoft's base app -- for example, "what does the event this codeunit subscribes to actually look like" or "what table does my page extension's SourceTable field point at." Today, in a single-tenant deployment, an object referenced from outside the indexed corpus becomes an opaque "external" stub with no signature, no body, and no further edges. With federation configured, that stub resolves through to the real object in the remote (public) instance's graph, and the agent gets the real signature/relationships instead of a dead end.

**Why this priority**: This is the entire reason federation is being built -- without it, private instances are structurally blind to everything outside their own corpus, which is most of the actual codebase (the base app) for a typical extension-only ISV or in-house team.

**Independent Test**: Configure a private instance's aggregator with one remote federation endpoint (the public instance), index a small private corpus containing an object that extends/subscribes-to/references a known Microsoft base app object, and confirm a graph query against the private instance returns the real remote object's data (not a stub) for that cross-boundary edge.

**Acceptance Scenarios**:

1. **Given** a private instance with federation enabled against the public instance, **When** a graph tool call resolves an edge whose target is an external stub in the private corpus, **Then** the response includes the real target object's data (name, signature location, further edges) sourced from the remote instance, clearly marked as coming from that remote source.
2. **Given** the same setup but the external stub's `global_id` has no match in any configured remote instance, **When** the same kind of query runs, **Then** the response falls back to today's stub behavior (object marked external, no crash, no hang).
3. **Given** a private instance with no federation endpoints configured, **When** any graph tool runs, **Then** behavior is byte-for-byte identical to the current single-tenant behavior (federation is strictly additive and opt-in).

---

### User Story 2 - Combined search across a private corpus and a trusted remote corpus (Priority: P2)

The same developer asks a general code-search question that isn't scoped to one object -- e.g. "how do other apps typically validate a posting date before posting a sales order." Their private corpus alone may have zero or few relevant examples (it's a small internal app suite); the answer they actually want is well-represented in the public base-app corpus. With federation, one search call against the private instance returns relevant results pulled from both corpora, each result labeled with which instance it came from.

**Why this priority**: Valuable and directly requested, but secondary to P1 -- graph-edge resolution is the harder problem and the one the `global_id` work specifically targets; full-text/semantic search federation is comparatively straightforward fan-out once the endpoint/trust plumbing from P1 exists.

**Independent Test**: With the same federation configuration as User Story 1, run a search query against the private instance's aggregator that has known matches in both the private and the public corpus, and confirm the merged result set includes matches from both, each labeled with its source instance.

**Acceptance Scenarios**:

1. **Given** federation is configured, **When** a search query has matches in both the private and a remote corpus, **Then** the response includes results from both, each tagged with its originating instance.
2. **Given** a configured remote endpoint is unreachable or times out, **When** a search query runs, **Then** the private instance still returns its own local results promptly, with a clear indication that one or more remote sources were unavailable rather than silently omitting them or failing the whole request.

---

### User Story 3 - Private data never leaves the private instance's trust boundary (Priority: P1)

The operator of a private instance needs confidence that turning on federation cannot leak their internal app's source code, object names, or any other private-corpus data to the remote instance, and that the remote (public) instance has no way to reach into or query the private instance. Federation is strictly outbound: the private instance is a client of the remote instance, never the reverse, and outbound requests carry only the minimum needed to resolve a lookup (e.g. an object's type and global identifier), never source text, file contents, or private object bodies.

**Why this priority**: This is a hard trust/safety constraint, not a nice-to-have -- a company will not enable a feature that risks exposing its internal codebase to a third party (even one they trust for read access to public data), and the public instance's operator does not want to become an unwitting relay or store of private companies' proprietary code.

**Independent Test**: With federation enabled, capture every outbound request the private instance's aggregator makes to the remote endpoint during a representative set of P1/P2 queries, and confirm no request body or header contains private-corpus source text, file paths outside the public corpus, or any private object's full body -- only lookup keys (global_id, object type, search terms the user themselves typed) cross the boundary. Confirm the remote instance's own tools have no code path that can enumerate or query the private instance.

**Acceptance Scenarios**:

1. **Given** federation is configured and active, **When** any federated request is made, **Then** the remote instance receives only the query/lookup payload (global_id, object type, or the user's own search terms) and never private-corpus source code, file contents, or object bodies.
2. **Given** a private instance operator wants to stop federating, **When** they remove or disable a remote endpoint from their configuration, **Then** no further requests are sent to that endpoint, with no other code or config changes required.
3. **Given** the public/remote instance, **When** examined for any capability to initiate a request toward a private instance, **Then** none exists -- the remote instance has no configuration or code path referencing any private instance's address.

### Edge Cases

- What happens when two different remote endpoints both have a real (non-stub) object for the same `global_id` (e.g. two companies both privately extend the same base app object and both happen to be configured as federation targets)? The system needs a deterministic tie-break (e.g. first configured endpoint wins, or the private instance's own corpus always wins if it has a real node) rather than silently picking one at random per request.
- What happens when a remote endpoint's version of an object is for a different, incompatible platform version than the private corpus was built against (e.g. private app targets `w1-28`, remote only has `w1-27` built)? The resolved cross-boundary data should be labeled with the remote instance's resolved version so a developer isn't misled into thinking it's the exact version their app runs against.
- What happens when a private operator configures a remote endpoint that itself federates further (a federation chain)? Federation depth should be bounded (e.g. one hop only) to avoid unbounded fan-out or cycles.
- What happens when the local corpus already has a *real* (non-stub) node for a given `global_id` (e.g. the private corpus vendors a copy of a base app table)? The local, private copy must always take priority over any remote result for that same id -- federation only fills genuine gaps, never overrides local truth.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A private instance's aggregator MUST support being configured with zero or more remote federation endpoints, each identifying a remote bc-code-atlas aggregator to query outward to. With zero endpoints configured, behavior MUST be identical to today's non-federated single-tenant behavior.
- **FR-002**: When a graph tool call would otherwise return an external/stub node, the system MUST attempt to resolve that stub's `global_id` against each configured remote endpoint and, on a match, substitute the real object's data (signature/location/further edges) in the response.
- **FR-003**: Every piece of data in a response that originated from a remote federated instance MUST be clearly labeled with which remote instance it came from, so a developer (or their agent) can distinguish "this is from my own private corpus" from "this is from a public/remote source."
- **FR-004**: Search queries against a private instance MUST be able to fan out to configured remote endpoints and return a merged result set spanning local and remote corpora, each result labeled with its source.
- **FR-005**: The system MUST NOT transmit private-corpus source code, file contents, or full object bodies to a remote endpoint under any federated operation. Outbound federated requests MUST carry only lookup keys (e.g. `global_id`, object type) or the user's own literal search terms.
- **FR-006**: Federation MUST be strictly directional: a private instance MUST be able to query a remote instance, and a remote/public instance MUST have no mechanism to initiate a request toward, enumerate, or discover any private instance.
- **FR-007**: A private operator MUST be able to add, remove, or disable a remote federation endpoint via configuration alone, without code changes or redeploying core services, and the change MUST take effect for new requests without requiring a full restart of dependent services beyond what a normal config reload already requires.
- **FR-008**: If a configured remote endpoint is unreachable, times out, or errors, the system MUST degrade gracefully -- returning local-only results plus a clear indication that a remote source was unavailable -- rather than failing the entire request.
- **FR-009**: When resolving a `global_id` that has a real (non-stub) node in both the local corpus and one or more remote instances, the local instance's own copy MUST take priority; remote resolution only fills stubs the local corpus cannot resolve itself.
- **FR-010**: When more than one configured remote endpoint could resolve the same stub, the system MUST apply a deterministic, documented precedence (e.g. endpoint configuration order) rather than a nondeterministic or random choice.
- **FR-011**: Federation MUST NOT chain beyond one hop -- a private instance querying a remote instance MUST NOT cause that remote instance to itself fan out to any of *its* configured remote endpoints on the private instance's behalf.
- **FR-012**: Any data resolved from a remote instance MUST retain enough version/corpus identification (e.g. which country/version the remote instance resolved it against) that a developer isn't misled about which exact build the cross-boundary data represents.

### Key Entities

- **Federation endpoint**: A remote bc-code-atlas aggregator a private instance is configured to query outward to. Has an address, optional credential/auth, and an enabled/disabled state; configured per private-instance deployment, not discovered automatically.
- **Global object identity (`global_id`)**: The existing stable, cross-corpus identifier stamped on every AL graph node (real or stub) by graphify-al, used as the join key between a local stub and a remote instance's real node for the same object.
- **Federated result**: A search or graph-query result item that includes source attribution (which instance -- local or a specific named remote -- it came from) and, where applicable, the remote instance's resolved country/version.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A structural graph question whose answer crosses from a private corpus into the public base-app corpus returns the real object's data (not an unresolved external stub) in at least 95% of cases where that object exists in a configured remote instance's corpus.
- **SC-002**: Enabling federation on an existing private deployment requires configuration changes only -- zero changes to the private operator's own indexed content or corpus build process.
- **SC-003**: A private operator can verify, through inspection of outbound federated traffic alone (without reading any code), that no private source code or file contents leave their deployment.
- **SC-004**: When a configured remote endpoint is completely unreachable, a federated query still returns local results within the same time budget as an equivalent non-federated query today, with no more than one bounded remote-timeout delay added.
- **SC-005**: Turning federation off (removing/disabling all remote endpoints) restores byte-for-byte identical responses to the pre-federation single-tenant behavior, verified by regression tests that run both configurations against the same corpus and diff the results.

## Assumptions

- The remote endpoint in the initial (and likely primary, long-term) use case is bc-code-atlas's own public instance serving the Microsoft base app (W1) and country localizations; the design should not hard-code that assumption, but validating against any other specific remote operator's instance is out of scope for this feature.
- Remote endpoints may require the same API-key/bearer-token authentication the aggregator's HTTP transport already supports for its own clients; a private operator configures a credential per remote endpoint the same way they'd configure a credential for any other upstream service, reusing that existing mechanism rather than inventing a new auth scheme.
- "Local corpus always wins" (FR-009) is the correct default precedence rather than "most recently built wins" or "remote always wins," because a private operator who has deliberately indexed something themselves should never have their own build silently overridden by a remote instance's copy.
- Search-result merging (User Story 2) does not need to produce a single globally-ranked interleaved list in this feature's initial scope -- results grouped or clearly labeled by source, with each source's own internal relevance ordering preserved, satisfies the requirement; true cross-instance relevance ranking is a possible future refinement, not a blocker here.
- Federation endpoints are configured explicitly by the private operator (a URL plus optional credential); there is no automatic discovery, registry, or marketplace of available remote instances, consistent with the stated non-goal.
- The existing `global_id` scheme (namespace, or `app:<publisher>::<name>` fallback, plus object type) is assumed sufficiently collision-resistant for federation's join purposes as-is; hardening or extending that scheme is graphify-al's concern and explicitly out of scope here.
