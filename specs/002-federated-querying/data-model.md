# Phase 1 Data Model: Federated Multi-Instance Querying

No new persistent storage is introduced (the aggregator remains stateless;
see Technical Context). The entities below are in-memory/config shapes.

## FederationEndpoint

Configuration describing one remote bc-code-atlas aggregator a private
instance is willing to query outward to.

| Field | Type | Notes |
|---|---|---|
| `url` | string (URL) | Base URL of the remote aggregator's MCP endpoint (e.g. `https://atlas.example.com/mcp`) |
| `credential_env_var` | string \| null | Name of an environment variable holding the bearer token/API key to send, if the remote requires auth. The value itself is never stored in config (R1). |
| `enabled` | bool | Default `true`. Operator can disable without removing the entry. |
| `order` | integer (implicit, = list position) | Determines FR-010 tie-break precedence; not a separate stored field, just list order. |

**Validation rules**:
- `url` MUST be a well-formed absolute URL.
- Duplicate `url` entries are rejected at config-load time (ambiguous
  precedence otherwise).
- List is empty by default -- federation is off unless explicitly configured
  (FR-001).

**Lifecycle**: Read once at aggregator startup from CLI flags/env (R1); a
config change requires a config reload (documented as acceptable in FR-007 --
"without requiring a full restart... beyond what a normal config reload
already requires"). No runtime mutation API in this feature's scope.

## GlobalObjectIdentity (`global_id`)

Not new -- this is the existing graphify-al node attribute
(`al://<qualifier>/<object type>/<name>`, or the `app:<publisher>::<name>`
qualifier fallback) already emitted on every real and stub AL node. Recorded
here only because it's the join key this feature's data model pivots on.

| Field | Type | Notes |
|---|---|---|
| `global_id` | string | Present on every node's response payload already; unchanged by this feature. |
| `file_type` | string | `"external"` marks a stub (existing field, used by federation logic to decide *whether* to attempt resolution -- see R2/R3). |

## FederatedResult

The shape a federation-aware tool response uses to attribute data to its
source. Not a new top-level MCP type -- an additive field on existing
response shapes, per Constitution Principle VII (no incidental new
structure).

| Field | Type | Notes |
|---|---|---|
| `_source` | string | `"local"` or the federation endpoint's configured identifier (e.g. its `url`, or a short label if one is added to `FederationEndpoint` later) for any node/result that came from a remote instance. Omitted entirely (not `_source: "local"`) for ordinary local-only responses when federation is not configured, so existing non-federated clients see no shape change (User Story 1, Acceptance Scenario 3). |
| `_remote_country` / `_remote_version` | string \| null | Present only on federated results (FR-012) -- the remote instance's resolved (country, version) for the returned data, distinct from any `country`/`version` the caller passed to the *local* instance. |

**Validation rules**:
- A response item MUST NOT carry both federation attribution fields and any
  private-corpus-only internal fields it wouldn't otherwise carry --
  federation only adds attribution, never new content categories.
- `_source`/`_remote_country`/`_remote_version` are present only when
  federation actually resolved something from a remote; a stub that
  remained unresolved (R3, no match found) is returned exactly as today,
  unchanged.

## Relationships

```text
FederationEndpoint (0..N, operator-configured)
        │ queried in list order (R3) when a stub is found
        ▼
GlobalObjectIdentity (global_id) ──── join key ────► real node on remote
        │
        ▼
FederatedResult (local response, enriched with _source/_remote_country/_remote_version)
```
