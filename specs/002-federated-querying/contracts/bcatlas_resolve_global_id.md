# Contract: `bcatlas_resolve_global_id` (new tool, graph server)

Added to `graphify-al/graphify/serve.py`'s MCP tool surface (same server that
already exposes `bcatlas_get_node`, `bcatlas_get_signature`, etc.), following
the existing tool conventions in that file exactly (routing via
`_resolve_ctx`, same error shapes as `_RoutingError`).

## Request

| Argument | Type | Required | Notes |
|---|---|---|---|
| `global_id` | string | yes | The `al://...` (or `app:...` fallback) identity to resolve, as emitted on any node's `global_id` field. |
| `country` | string \| null | no | Existing multi-tenant routing arg (must pair with `version`, same rule as every other routed tool). |
| `version` | string \| null | no | Existing multi-tenant routing arg (exact `commit_sha`). |

## Response (success -- match found, real node)

Same shape `bcatlas_get_node` returns for a real (non-stub) node: full node
attributes (`label`, `file_type`, `source_location`, `global_id`, etc.) plus
its edges, unchanged from that tool's existing contract. No new fields are
introduced on the node payload itself -- this tool differs from
`bcatlas_get_node` only in its lookup key (`global_id` instead of `label`).

## Response (no match)

A clear, structured "not found" result (matching this server's existing
not-found convention for `bcatlas_get_node` on an unknown label) --
distinguishable from a real node response so the aggregator's federation
logic (R3) can tell "no remote has this" apart from "here's the real node."

## Error cases

- Both `country` and `version` must be supplied together, or neither --
  identical rule and error message to every other routed tool in this
  server (`_resolve_ctx`).
- Malformed `global_id` (doesn't match the `al://...`/`app:...` shape) --
  returns a clear validation error, not a silent empty result.

## Non-goals for this tool

- Does not accept a bare `label` -- that's what `bcatlas_get_node` is for;
  this tool exists specifically because `global_id` is the only reliable
  cross-instance join key (see research.md R2).
- Does not itself federate further (Constitution Check / R3's structural
  one-hop bound) -- it has no federation-endpoint parameter at all.
