# Feature Specification: Optional Continuous Re-Index (Watch Mode)

**Feature Branch**: `007-file-watcher-reindex`

**Created**: 2026-08-12

**Status**: Draft

**Input**: User description: "Right now, indexing runs on demand -- either through an explicit index command or automatically before each search request. I'd like an optional watch mode that re-indexes changed files right away, without waiting for the next search request. This corresponds to GitHub issue #21 in StefanMaron/bc-code-atlas. This feature must be opt-in (default off, current on-demand-refresh behavior unchanged) and must NOT be enabled on or trigger any behavior change on the hosted production instance -- that instance's default corpus must not get a watcher unless an operator explicitly opts in, and this session must not touch or restart the hosted VM to implement or verify it."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operator gets fast feedback while iterating on a source directory (Priority: P1)

An operator actively editing AL source in a directory the search service is pointed at (for example, while developing against a custom local directory per the existing configurable-source-directory capability) wants search results to reflect their latest edits without having to manually trigger a reindex or issue a throwaway search first.

**Why this priority**: This is the entire point of the feature (GitHub issue #21) — without it, an operator must remember to trigger indexing themselves, which breaks the "edit, then immediately search" workflow this exists to support.

**Independent Test**: Enable continuous reindexing, change a source file's content, then search for that new content without first issuing any indexing command or a search with an explicit refresh — confirm the new content is found.

**Acceptance Scenarios**:

1. **Given** continuous reindexing is enabled for a source directory, **When** an operator edits a file under that directory, **Then** the change becomes findable via search within a short, bounded time, without the operator having triggered indexing themselves.
2. **Given** continuous reindexing is enabled, **When** an operator deletes a file under the source directory, **Then** that file's content is no longer returned by search within the same short, bounded time.

---

### User Story 2 - Operator who hasn't enabled it sees no behavior change (Priority: P2)

An operator running the search service exactly as before — including the hosted default Business Central corpus — must see identical behavior to today: indexing still only happens on an explicit index action, or automatically as part of a search request that asks for a refresh.

**Why this priority**: Regression safety. This feature must be strictly additive; the existing hosted instance and any operator who doesn't opt in must be completely unaffected — this is a harder requirement than usual for this project (constitution Principle VIII: the hosted instance's always-warm serving daemon must never get surprise new behavior).

**Independent Test**: Do not enable continuous reindexing; confirm all existing indexing/search behavior (on-demand index command, search's own refresh behavior) works exactly as it did before this feature existed.

**Acceptance Scenarios**:

1. **Given** continuous reindexing is not enabled, **When** a file changes under the source directory, **Then** that change is not reflected in search results until an explicit index action or a refreshing search request occurs — exactly as today.
2. **Given** the hosted default corpus's configuration is unchanged (no explicit opt-in added), **When** the service is deployed or restarted, **Then** it behaves exactly as it did before this feature existed.

---

### User Story 3 - Many changes happen at once without overloading the system (Priority: P3)

An operator with continuous reindexing enabled performs an operation that changes many files nearly simultaneously (for example, switching branches or a bulk find-and-replace across the source directory), and the system handles this gracefully rather than attempting one reindex per individual file change.

**Why this priority**: Lower priority because it's a robustness property of User Story 1's mechanism rather than a distinct capability, but important enough to specify explicitly — an ungraceful implementation could turn a routine bulk edit into resource contention or a flood of redundant work.

**Independent Test**: Change many files in a short window; confirm the system performs a small, bounded number of reindex operations covering all the changes, not one per file.

**Acceptance Scenarios**:

1. **Given** continuous reindexing is enabled, **When** many files change within a short window of each other, **Then** the system coalesces this into a small number of reindex operations rather than one per changed file.

---

### Edge Cases

- What happens if the mechanism responsible for continuous reindexing itself fails or stops running? This MUST be detectable (e.g., visible in server logs) rather than silently and invisibly leaving the index stale forever; the existing on-demand refresh path (an explicit index action, or a search request's own refresh behavior) MUST remain available as a fallback regardless of whether continuous reindexing is enabled or has failed.
- What happens if an operator enables this for a very large source directory (such as the full default Business Central corpus)? The feature MUST still function, but this is an explicit operator choice with its own resource cost — it is never applied automatically to any corpus, including the hosted default one.
- What happens to search requests while a continuous reindex operation is in progress? Concurrent search requests MUST continue to be served without being blocked or meaningfully degraded by the background reindexing activity.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The search service MUST support an explicit, opt-in configuration option that enables continuous reindexing of its configured source directory, disabled by default.
- **FR-002**: When continuous reindexing is not enabled, all indexing behavior MUST remain exactly as it is today — only an explicit index action or a search request's own refresh behavior triggers indexing.
- **FR-003**: When continuous reindexing is enabled, a change to a file under the configured source directory MUST become reflected in search results within a short, bounded time, without requiring a search request or explicit index action to trigger it.
- **FR-004**: Enabling continuous reindexing MUST be a per-instance, explicit operator choice — it MUST NOT be automatically enabled for any corpus, and in particular MUST NOT be enabled for the hosted default Business Central corpus without an operator explicitly configuring it there.
- **FR-005**: Multiple file changes occurring within a short window of each other MUST be coalesced into a small, bounded number of reindex operations rather than one operation per individual file change.
- **FR-006**: If the continuous reindexing mechanism fails or stops functioning, this MUST be visible (e.g., logged) rather than silently and invisibly leaving the index stale.
- **FR-007**: Continuous reindexing activity MUST NOT block or meaningfully degrade concurrent search requests.
- **FR-008**: Enabling continuous reindexing MUST NOT change any existing tool's name, request shape, or response shape — only indexing timing is affected.

### Key Entities

- **Continuous Reindexing Configuration**: The operator-supplied setting that enables continuous reindexing and controls how promptly it reacts to changes. Read once at service startup; disabled unless explicitly configured.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can enable fast, automatic reindexing by configuration alone, with zero source code changes.
- **SC-002**: A changed file becomes searchable, without any explicit search-triggered refresh or manual index action, within a short and predictable time window after being enabled.
- **SC-003**: 100% of existing on-demand indexing/search behavior is unchanged when continuous reindexing is not enabled.
- **SC-004**: A burst of many simultaneous file changes results in a small, bounded number of reindex operations rather than one per changed file, keeping resource usage proportional to how often changes happen, not how many files change at once.
- **SC-005**: The hosted default Business Central corpus's behavior is unaffected unless an operator explicitly opts it in.

## Assumptions

- This feature applies to the same single default-corpus-serving search process configured by the existing configurable-source-directory (opt-in local AL directory) and configurable-instructions capabilities delivered earlier this session — it does not add continuous reindexing to the separate multi-tenant registry/build pipeline's per-(country, version) artifacts, which are immutable once built and are out of scope here.
- "Short, bounded time" means on the order of a few seconds for a typical development workflow, not sub-second real-time reaction — this is a development/operator convenience feature, not a latency-critical guarantee.
- The hosted production instance is not touched by this work; verification is local-only, consistent with the constraint already applied to the two features implemented earlier this session.
