# Feature Specification: Configurable MCP Instructions and Path Filtering

**Feature Branch**: `006-configurable-mcp-instructions`

**Created**: 2026-08-12

**Status**: Draft

**Input**: User description: "The search server's MCP tool instructions and path filtering currently assume the Microsoft Business Central source and documentation corpus. I'd like these to be configurable through a settings file, with the current behavior kept as the default, so the server can present different instructions and filtering rules when it's set up against a different source. This corresponds to GitHub issue #20 in StefanMaron/bc-code-atlas, and follows directly from issue #18 (specs/005-local-source-directory, already implemented this session), which lets the search/chunker service index any local AL source directory via BCATLAS_SOURCE_DIR — but that work deliberately left the server's hardcoded MCP instructions text (which explicitly says 'Microsoft Dynamics 365 Business Central') and the corpus-path-prefix expansion (currently a fixed candidate list: w1-28-src, docs, docs-devitpro) unchanged. Preserve current default behavior exactly when no override is configured — this is an additive config option, not a breaking change to the default hosted BC corpus."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operator indexing a non-BC source sees accurate tool instructions (Priority: P1)

An operator who has pointed the search service at a custom, non-Microsoft-BC AL source directory (per issue #18) wants the MCP server's own tool description/instructions to accurately describe what's actually indexed, so a connecting agent doesn't get told it's searching "Microsoft Dynamics 365 Business Central" when it isn't.

**Why this priority**: This is the core problem the issue names — a misleading tool description actively misinforms every calling agent, not just a cosmetic gap. The project's own constitution (Principle VII) treats inaccurate agent-facing descriptions as a defect, not a nice-to-have.

**Independent Test**: Configure custom instructions text via settings, start the server pointed at a non-BC directory, connect an MCP client, and confirm the returned server instructions match the configured text, not the default BC-specific text.

**Acceptance Scenarios**:

1. **Given** an operator has configured custom MCP instructions text, **When** an MCP client connects to the server, **Then** the client receives that custom text as the server's instructions.
2. **Given** no custom instructions are configured, **When** an MCP client connects, **Then** the client receives exactly the same default Business Central instructions text the server has always returned.

---

### User Story 2 - Operator configures path-filtering behavior for a differently-laid-out corpus (Priority: P2)

An operator whose custom source directory doesn't share the default corpus's submodule-style layout (`w1-28-src/`, `docs/`, `docs-devitpro/`) wants search path-filter expansion to match their own directory structure instead, so path-based search filters behave sensibly for their layout.

**Why this priority**: Secondary to accurate instructions (User Story 1) because the existing path-prefix expansion already degrades safely (no expansion attempted) against an arbitrary directory — this story is about giving an operator control to make filtering *actively useful* for their own layout, not about fixing a defect.

**Independent Test**: Configure a custom set of path prefixes via settings, issue a `bcatlas_search` call with a prefix-agnostic path filter, and confirm results are expanded against the configured prefixes instead of the default candidate list.

**Acceptance Scenarios**:

1. **Given** an operator has configured a custom list of path prefixes, **When** a search request includes a `paths` filter without one of those prefixes, **Then** the server also tries the filter with each configured prefix prepended, the same way it does today for the default corpus's prefixes.
2. **Given** no custom path prefixes are configured, **When** a search request is made, **Then** path-prefix expansion behaves exactly as it does today (checking for the existing default candidate list under the server's project root).

---

### User Story 3 - Operator updates configuration without needing a code change or redeploy of server logic (Priority: P3)

An operator wants to change instructions text or path-filter prefixes by editing a settings file, not by modifying and redeploying the search server's code.

**Why this priority**: Lower priority because it's implied by "configurable through a settings file" rather than being a distinct behavior to test beyond User Stories 1 and 2 — listed separately to make the settings-file requirement explicit and testable on its own.

**Independent Test**: Change the settings file's instructions text, restart the server (no code edits), and confirm the new text is served.

**Acceptance Scenarios**:

1. **Given** a running server previously started with one settings file, **When** an operator edits the settings file's instructions text and restarts the server, **Then** the newly configured text is what's served — no source code changes were required.

---

### Edge Cases

- What happens when the settings file exists but is malformed or missing expected fields? The server MUST fail to start with a clear error identifying the problem, rather than starting with partially-applied or silently-defaulted configuration that doesn't match what the operator intended.
- What happens when a custom path-prefix list is configured but is empty? Treated the same as "no custom prefixes configured" for that specific behavior — no prefix expansion attempted, matching how the default corpus's own dynamic detection already degrades when none of its candidate directories exist.
- How does this interact with issue #18's `BCATLAS_SOURCE_DIR`? Independent settings — an operator can change the source directory, the instructions text, and the path prefixes separately; none of the three requires setting the others.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The search server MUST support configuring its MCP-reported instructions text via a settings file, without requiring a source code change.
- **FR-002**: When no instructions override is configured, the server MUST report exactly the current default Business Central instructions text — unchanged from today's behavior.
- **FR-003**: The search server MUST support configuring the list of path prefixes used to expand prefix-agnostic search filters, via the same settings file, without requiring a source code change.
- **FR-004**: When no path-prefix override is configured, the server MUST continue to determine path prefixes exactly as it does today (checking which of the existing default candidate subdirectories exist under the server's project root).
- **FR-005**: The server MUST fail to start with a clear, actionable error if a settings file is present but cannot be parsed or is otherwise invalid, rather than starting with partial or silently-defaulted configuration.
- **FR-006**: Configuring these settings MUST NOT change any other MCP tool's name, request schema, or response schema — only the instructions text and path-prefix-expansion behavior are affected.
- **FR-007**: This feature MUST NOT alter the hosted default Business Central instance's served instructions or path-filtering behavior unless an operator explicitly adds a settings override there.

### Key Entities

- **Server Presentation Settings**: Operator-supplied configuration read at server startup — the MCP instructions text (a string) and the path-prefix list (an ordered list of strings) used for search filter expansion. Both are independently optional; each falls back to today's hardcoded default when absent.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can change what a connecting agent is told about the indexed corpus by editing configuration alone, with zero source code changes.
- **SC-002**: An operator whose custom directory has its own layout can make prefix-agnostic path filters work correctly against that layout by editing configuration alone.
- **SC-003**: 100% of existing default-corpus behavior (instructions text, path-filter expansion) is unchanged when no override is configured.
- **SC-004**: A malformed settings file is caught at startup, not discovered later as confusing or wrong search behavior.

## Assumptions

- This feature only affects `chunker/mcp_http_server.py`'s own default-corpus-serving instance (the same process configured by issue #18's `BCATLAS_SOURCE_DIR`) — it does not add per-(country, version) instruction overrides to the separate multi-tenant registry/build pipeline, which is out of scope here.
- "Settings file" follows the same shape and mechanism as issue #18's per-directory configuration approach (a file alongside the indexed project), for consistency, rather than introducing a second, differently-shaped configuration mechanism.
- The hosted production instance is not touched by this work; verification is local-only, consistent with the constraint already applied to issue #18 this session.
