# Feature Specification: Multi-Country, Multi-Version Serving

**Feature Branch**: `001-multi-version-serving`

**Created**: 2026-07-02

**Status**: Draft

**Input**: User description: "Serve bc-code-atlas across every country localization and every
shipped version of Business Central's AL source, not just the current single-version (w1-28)
local setup." Three prioritized user stories: (P1) version/country discovery and resolution,
(P2) diffing across versions at file/symbol granularity plus multi-step change history, (P3)
querying a completely different, previously-untouched (country, version) pair end-to-end.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Discover and resolve a version (Priority: P1)

A developer's coding agent wants to investigate a specific country/version of Business
Central it doesn't have exact details for. It doesn't know which countries exist, which
versions of a country are available, or the exact identifier for "latest 28.1" — it needs
to find that out before it can ask for anything else.

**Why this priority**: every other capability (diffing, querying a different version) needs
a resolved, exact version first. Without this, an agent can only ever guess at spec strings
and fail silently or noisily.

**Independent Test**: from a separate MCP client session, list available countries, list
available versions for one country, and resolve both an exact build string and a loose spec
("latest 28.1") to a single unambiguous version identifier — without touching any other tool.

**Acceptance Scenarios**:

1. **Given** no prior knowledge of what's available, **When** the agent asks what countries
   are supported, **Then** it receives a finite, human-usable list (not a raw dump of every
   branch name) it can choose from.
2. **Given** a chosen country, **When** the agent asks what versions exist for it, **Then**
   it receives a usable summary of available major versions (not a list of thousands of
   individual builds).
3. **Given** an exact build identifier (e.g. `w1-28.2.50931.52151`), **When** the agent
   resolves it, **Then** it gets back the same identifier confirmed as valid, unambiguous,
   and ready to use in other tools.
4. **Given** a loose version spec (e.g. "latest 28.1"), **When** the agent resolves it,
   **Then** it gets back exactly one exact build identifier — the newest build matching that
   spec — not a list requiring further disambiguation.
5. **Given** a country or version spec that doesn't exist, **When** the agent asks for it,
   **Then** it gets a clear "not found" response with no silent fallback to a wrong version.

---

### User Story 2 - Diff across versions (Priority: P2)

A developer's coding agent wants to know what changed in a specific object or procedure
between two versions of the same country — either "what's different between these two
exact points" or "show me every point in between where this specific procedure changed."

**Why this priority**: this is a concrete, high-value capability testers already asked for
by name, and it's independently useful once P1 exists, without needing P3's full on-demand
build/serve infrastructure (diffing works directly against source history).

**Independent Test**: from a separate MCP client session, request a diff for one real
procedure between two resolved versions, and separately request the full change history of
that procedure across a wider version range — both without any other tool having been
called first except version resolution from User Story 1.

**Acceptance Scenarios**:

1. **Given** a country, a symbol (object or procedure name), and two resolved versions,
   **When** the agent requests a diff, **Then** it receives only that symbol's before/after
   text or a diff of it — not a file-level or repository-level diff.
2. **Given** a country, a file path, and two resolved versions, **When** the agent requests
   a diff scoped to that file, **Then** it receives only that file's changes.
3. **Given** no path or symbol is supplied, **When** the agent requests a diff, **Then** the
   request is rejected with guidance to scope it — an unscoped whole-repository diff is
   never produced.
4. **Given** a symbol and a version range spanning multiple real changes, **When** the agent
   requests the change history, **Then** it receives an ordered sequence of only the points
   where that symbol's own text changed — not every commit that merely touched the
   containing file.
5. **Given** the same request, **When** the agent asks for start/end only versus every
   intermediate step, **Then** the response shape matches what was asked for.

---

### User Story 3 - Query a completely different version end-to-end (Priority: P3)

A developer's coding agent wants real semantic search and structural graph answers for a
(country, version) pair that has never been requested before — including one with no warm
data at all — and is willing to wait for it to become available.

**Why this priority**: this is the most valuable capability (arbitrary version coverage, not
just diffing) but also the most infrastructure-heavy, so it depends on P1 (to resolve what
was requested) and benefits from, but doesn't strictly require, P2.

**Independent Test**: from a separate MCP client session, request a (country, version) pair
known to have zero warm data, observe an explicit "this will take a while" acknowledgment,
then — once ready — run a real semantic search and a real structural graph query against it
and get results grounded in that exact version's source, not a different one.

**Acceptance Scenarios**:

1. **Given** a resolved (country, version) with no existing warm data, **When** the agent
   requests it, **Then** it receives an immediate acknowledgment that building has started
   and an estimate that this takes real time, rather than blocking silently or timing out.
2. **Given** a build in progress, **When** the agent checks status or queries too early,
   **Then** it gets a clear "not ready yet" response, never partial or wrong-version data.
3. **Given** a build has completed, **When** the agent runs semantic search or a structural
   graph query against that (country, version), **Then** results are grounded in that exact
   version's real source — verifiably different from results against a different version of
   the same country.
4. **Given** a (country, version) that shares most of its content with an already-warm
   sibling (same country, nearby version, or a different country with high content overlap),
   **When** it's requested, **Then** it becomes available meaningfully faster than the first
   ever build of that country took, reflecting genuine reuse of the already-known content.
5. **Given** limited disk/compute and multiple (country, version) pairs sitting idle,
   **When** the system needs room for a new request, **Then** it reclaims idle capacity
   automatically rather than refusing new requests indefinitely or growing without bound.
6. **Given** a (country, version) whose warm data was reclaimed, **When** it's requested
   again later, **Then** it becomes available again (rebuilt), not permanently lost.

### Edge Cases

- What happens when a version spec matches more than one build ambiguously (e.g. a spec too
  loose to resolve to exactly one build)? Resolution MUST fail explicitly rather than
  silently pick one.
- What happens when a symbol used in a diff or change-history request exists in one of the
  two versions but not the other (added or removed between them)? MUST be reported as such,
  not treated as an error.
- What happens when two concurrent requests ask for builds of different (country, version)
  pairs at the same time and available build capacity is smaller than the number of
  requests? Requests MUST queue rather than fail or run in a way that risks builds
  corrupting each other's data.
- What happens when a request arrives for a (country, version) that is currently mid-build
  from an earlier, still-in-flight request? The second request MUST reuse the in-flight
  build rather than starting a redundant duplicate.
- What happens when the upstream source-history repository itself is unreachable at request
  time (e.g. resolving a version or fetching a historical commit fails)? MUST surface a
  clear upstream-unavailable error, not a hang or a stale/wrong result presented as current.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST let a caller list available countries without prior knowledge of
  what exists.
- **FR-002**: System MUST let a caller list/describe available versions for a given country
  in a form usable by a caller that cannot enumerate thousands of raw entries itself.
- **FR-003**: System MUST resolve an exact version identifier to itself, confirming validity.
- **FR-004**: System MUST resolve a loose version spec (e.g. "latest within a stated major/
  minor range") to exactly one exact version identifier.
- **FR-005**: System MUST reject an unresolvable or ambiguous version spec explicitly rather
  than guessing.
- **FR-006**: System MUST produce a diff between two resolved versions of the same country,
  scoped to either an explicit file path or a resolved symbol (object/procedure).
- **FR-007**: System MUST refuse to produce a diff with no file or symbol scope supplied.
- **FR-008**: System MUST produce an ordered, multi-step change history for a given symbol
  across a version range, including only the points where that symbol's own resolved text
  changed.
- **FR-009**: System MUST let a caller choose between a start/end-only view and a full
  step-by-step view of a symbol's change history.
- **FR-010**: System MUST accept a request for any (country, version) pair that resolves to
  a real commit in the upstream source-history repository, with no country or version
  hardcoded as the only option.
- **FR-011**: System MUST acknowledge a build request immediately, distinct from returning
  final results, when the requested (country, version) is not already warm.
- **FR-012**: System MUST NOT allow a query against a (country, version) to return results
  before that version's build has completed.
- **FR-013**: System MUST NOT allow a query-serving read and a build-writing process to hold
  concurrent access to the same on-disk served artifact.
- **FR-014**: System MUST reuse already-known content when building a (country, version)
  pair that overlaps significantly with already-warm data, rather than always rebuilding
  from nothing.
- **FR-015**: System MUST bound how much warm data is kept resident using an automatic
  reclamation policy, rather than growing without bound or capping the number of distinct
  (country, version) pairs ever servable.
- **FR-016**: System MUST make a previously-reclaimed (country, version) available again on
  request (rebuildable), never permanently unavailable.
- **FR-017**: System MUST coalesce concurrent build requests for the same (country, version)
  into a single build rather than duplicating work.
- **FR-018**: All capabilities in this feature MUST be reachable the same way existing
  capabilities are today — as MCP tools over HTTP, verifiable from a separate client
  session, never requiring direct source access by the calling agent.

### Key Entities

- **Country**: a localization of Business Central's base application, identified by its
  short code (e.g. `w1`, `us`, `de`); has its own independent line of versions.
- **Version**: one specific build of one country, uniquely identified by an exact build
  string; immutable once it exists — its content never changes after the fact.
- **Version Spec**: a caller-supplied identifier for a version, either exact or loose
  (resolves to the newest matching exact version).
- **Symbol**: a named object or a named procedure/trigger within an object, addressable
  independent of which version it's being looked up in.
- **Build**: the process of producing warm, servable (search + graph) data for one
  (country, version) pair; has a state (queued, in progress, ready) and a resource cost.
- **Warm Residency**: the set of (country, version) pairs currently kept servable without a
  new build; subject to automatic reclamation under resource pressure.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A caller with zero prior knowledge of available countries/versions can reach a
  single, exact, resolved version identifier in at most two tool calls.
- **SC-002**: A scoped diff (file or symbol) for two resolved versions returns only content
  relevant to that scope — verified by the response size being proportional to the scope,
  not to the size of the whole corpus.
- **SC-003**: An unscoped diff request is never fulfilled — 100% of such requests receive an
  explicit rejection instead of a large or truncated result.
- **SC-004**: A symbol's multi-step change history omits every containing-file change that
  didn't actually alter that symbol's own text — verified against at least one real symbol
  known (from prior manual measurement) to sit in a frequently-touched shared file.
- **SC-005**: Requesting a never-before-seen (country, version) pair succeeds end-to-end
  (build acknowledged, then real search and graph results against that exact version)
  without any manual intervention between the request and the result.
- **SC-006**: Building a (country, version) pair that shares the bulk of its content with an
  already-warm sibling completes in substantially less time than that sibling's own first
  build took — demonstrating real reuse, not a coincidence of similar corpus size.
- **SC-007**: The system continues accepting requests for new (country, version) pairs
  indefinitely without manual disk/memory intervention, by reclaiming idle warm data
  automatically under resource pressure.
- **SC-008**: A (country, version) pair that was reclaimed and later re-requested becomes
  available again without data loss or manual recovery steps.
- **SC-009**: Every capability introduced by this feature is verified from a real, separate
  MCP client session against live servers — not simulated or verified by direct source
  inspection.

## Assumptions

- The upstream source-history repository (`StefanMaron/MSDyn365BC.Sandbox.Code.History`)
  remains the sole source of truth for available countries/versions; no independent catalog
  of "supported" countries/versions is maintained separately from what that repository
  actually contains.
- "Country" and "version" granularity matches the existing branch/commit structure of that
  repository (one branch per country+major-version line, one commit per build) — this
  feature does not introduce a different versioning scheme.
- Build resource limits (how many concurrent builds, how much warm data stays resident) are
  configuration, not something this spec fixes to a specific number — the requirement is
  that a bound exists and is enforced automatically, not what the bound's value is.
- "Significantly overlapping" content for reuse purposes is judged by real content diff
  size, consistent with how this was measured during design (same-country version hops and
  cross-country pairs were both found to have large overlap in practice) — not by git commit
  ancestry, which does not reliably indicate content similarity for this repository.
- A single local/self-hosted deployment is assumed (consistent with the project's current
  operating model); this feature does not itself add multi-region or geo-distributed
  hosting.
