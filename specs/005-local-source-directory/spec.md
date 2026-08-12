# Feature Specification: Configurable Local AL Source Directory

**Feature Branch**: `005-local-source-directory`

**Created**: 2026-08-12

**Status**: Draft

**Input**: User description: "Allow the search service to index arbitrary local AL source directories. Right now the indexing setup (chunker + search server config) hard-assumes the Microsoft Business Central sparse-checkout layout (data/w1-28-src style, country/version pairs from the registry). Add a config option that points the search/chunker layer at any local directory of AL files instead, so it can index AL source from other locations (e.g. a customer's own AL project) without changing code. This corresponds to GitHub issue #18 in StefanMaron/bc-code-atlas. Preserve all existing multi-country/multi-version behavior as the default when no override is given — this is an additive config option, not a replacement of the registry-driven build pipeline."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operator indexes a non-Microsoft AL project (Priority: P1)

An operator running the search/chunker service locally (or as a self-hosted instance) wants to index a local directory of AL source that isn't the Microsoft BC sparse checkout — for example, a customer's own AL extension project — so their coding agent can search it the same way it searches the default corpus.

**Why this priority**: This is the entire point of the feature (GitHub issue #18); without it there is no way to point the service at anything other than the built-in Microsoft corpus.

**Independent Test**: Point the service's config at a local folder containing a handful of `.al` files (not under `data/w1-28-src`), start indexing, and confirm the files become searchable and their contents are returned correctly — without touching any code.

**Acceptance Scenarios**:

1. **Given** a local directory containing valid AL source files, **When** an operator sets the configured source path to that directory and starts the search service, **Then** the service indexes those files and they become searchable.
2. **Given** no source override is configured, **When** the search service starts, **Then** it indexes the existing default Microsoft BC corpus exactly as it does today.
3. **Given** an operator has configured a custom local source directory, **When** they query search, **Then** results reference file paths relative to that custom directory, not the default corpus's internal layout.

---

### User Story 2 - Operator points the service at a directory that doesn't exist or has no AL files (Priority: P2)

An operator misconfigures the source path (typo, wrong directory, empty directory) and needs a clear signal rather than a silent empty index or a crash.

**Why this priority**: Prevents a confusing "search returns nothing" support burden with no indication of the actual cause.

**Independent Test**: Configure a nonexistent path and start the service; confirm it fails fast with a clear error identifying the missing path, rather than starting up with a silently empty index.

**Acceptance Scenarios**:

1. **Given** a configured source path that does not exist on disk, **When** the search service starts, **Then** it fails to start and reports which configured path is missing.
2. **Given** a configured source path that exists but contains zero `.al` files, **When** the search service starts, **Then** it logs a clear warning that the index will be empty, rather than failing silently.

---

### User Story 3 - Operator switches back to the default corpus (Priority: P3)

An operator who previously configured a custom local directory wants to revert to indexing the default Microsoft BC corpus without any leftover state from the custom directory bleeding into results.

**Why this priority**: Lower priority because it's a reversal of User Story 1, but still needs to behave correctly — a custom corpus and the default corpus must not be able to cross-contaminate.

**Independent Test**: Configure a custom directory, index it, then remove the override and restart; confirm the service serves the default corpus and no longer returns results from the previously configured custom directory.

**Acceptance Scenarios**:

1. **Given** a service previously indexed a custom local directory, **When** the operator removes the override and restarts, **Then** search results come only from the default corpus.

---

### Edge Cases

- What happens when the configured local directory overlaps with (is a parent or child of) the default corpus's own on-disk location? The service MUST treat this as a configuration error and refuse to start, rather than silently indexing overlapping content twice.
- How does the system handle a configured path that is a file, not a directory? Fails fast with a clear error, same handling as User Story 2's "path does not exist" case.
- How does the system handle the existing multi-country/multi-version build pipeline (registry, on-demand `bcatlas_request_version` builds) while a custom local directory is configured? Out of scope for this feature — the custom local directory is an independent, single-corpus mode alongside (not a replacement for) the registry-driven multi-tenant pipeline; see Assumptions.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The search/chunker service MUST support an explicit configuration option (not a code change) that specifies a local filesystem directory as the AL source to index, in place of the built-in default Microsoft BC corpus.
- **FR-002**: When no such configuration option is set, the service MUST index the existing default Microsoft BC corpus exactly as it does today — this feature is additive and MUST NOT change default behavior.
- **FR-003**: When a custom local source directory is configured, the service MUST index only `.al` files found under that directory (recursively), independent of any Microsoft-specific directory layout assumptions.
- **FR-004**: The service MUST validate the configured path at startup: it MUST fail to start with a clear, actionable error message if the path does not exist or is not a directory.
- **FR-005**: The service MUST warn clearly (without failing to start) if the configured directory exists but contains no `.al` files.
- **FR-006**: Search results served from a custom local source directory MUST report file paths relative to that configured directory, not the internal path structure the default corpus uses.
- **FR-007**: The service MUST NOT mix indexed content from the default corpus and a custom local source directory in the same served index at the same time.
- **FR-008**: Existing MCP tool behavior (tool names, response shape) MUST remain unchanged when serving a custom local source directory — only the underlying indexed content differs.

### Key Entities

- **Source Configuration**: The operator-supplied setting that determines what gets indexed — either "default corpus" (current behavior) or "custom local directory" (a single filesystem path). Read at service startup.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can start indexing a new local AL directory by changing configuration alone, with zero source code changes.
- **SC-002**: Search results returned from a custom-configured local directory are indistinguishable in structure/quality from results returned against the default corpus (same tool behavior, same response shape).
- **SC-003**: Misconfiguration (missing/invalid path) is reported at startup, not discovered later as silently empty search results.
- **SC-004**: 100% of existing default-corpus behavior (search quality, tool responses) is unaffected when no custom source directory is configured.

## Assumptions

- This feature targets a single self-hosted/local instance indexing one corpus at a time (default OR one custom local directory), not simultaneous multi-tenant serving of arbitrary local directories alongside the registry-driven multi-country/multi-version pipeline — that pipeline (build queue, registry, per-(country,version) warm artifacts) is unaffected and remains the only way to serve multiple concurrent corpora.
- "Local directory" means a path already present on the filesystem where the service runs; fetching/cloning source from a remote location into that directory is the operator's own responsibility and out of scope.
- The custom directory is expected to contain plain `.al` files in any layout; no `app.json`/AL project manifest is required for indexing to work, since the existing default corpus is a raw source checkout without such manifests either.
- This feature does not need to touch the hosted production instance's default corpus or trigger any reindex there — it only adds an opt-in configuration path for other deployments/local use.
