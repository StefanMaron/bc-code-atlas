# Contract: Aggregator federation configuration surface

## CLI

```text
--federation-endpoint URL[|CREDENTIAL_ENV_VAR]   # repeatable, 0+ times
```

## Environment

```text
BCATLAS_FEDERATION_ENDPOINTS="URL1[|ENV1],URL2[|ENV2],..."
```

CLI flags and the env var are additive (both may be used); CLI order then
env-var-list order together form the full precedence list for FR-010, CLI
entries first.

## Behavioral contract

- Empty/unset (the default): every existing aggregator tool behaves exactly
  as it does today -- zero federation code paths are exercised (verified by
  User Story 1 Acceptance Scenario 3 / SC-005's regression-diff test).
- One or more endpoints configured: `bcatlas_search` and every graph tool
  whose response can contain an `external`-typed node (`bcatlas_get_node`,
  `bcatlas_query_graph`/traversal tools, `bcatlas_get_signature`, etc.) gain
  federation behavior as described in this feature's spec (User Stories 1-2),
  governed by research.md's precedence (R3) and timeout (R4) rules.
- Malformed endpoint entries (bad URL, referenced env var not set at
  startup) fail aggregator startup with a clear error identifying which
  entry is invalid -- not a silent skip, so a private operator's
  misconfiguration is caught immediately rather than discovered later as
  "federation just isn't working."

## Existing tool descriptions (Constitution Principle VII obligation)

Any tool whose behavior changes when federation is active MUST state so in
its own MCP tool description when federation is configured for that server
instance (description text is generated per-instance at startup, not
static), so a calling agent knows a response may include cross-instance
data before it happens -- not discovered by surprise.
