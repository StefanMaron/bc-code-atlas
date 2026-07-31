# Quickstart: Validating Federated Multi-Instance Querying

## Prerequisites

- Two bc-code-atlas stacks available: the existing public/default one
  (acting as "remote") and a second, smaller one standing in for a private
  operator's instance -- can be two local ports on the same machine for
  validation purposes; no separate hardware required.
- The private-instance corpus MUST contain at least one AL object that
  `extends`/`subscribes`/references a real object that exists in the remote
  instance's corpus (e.g. index a tiny test app that extends a known base
  app table), so User Story 1 has something real to resolve.
- `bcatlas_resolve_global_id` (contracts/bcatlas_resolve_global_id.md) is
  implemented and served by the remote instance's graph server.

## Setup

```bash
# 1. Start (or point to) the remote/public aggregator as usual -- no change.

# 2. Start the private instance's aggregator with federation enabled:
cd aggregator
uv run python unified_mcp_server.py \
  --search-url http://localhost:8801/mcp \
  --graph-url http://localhost:8802/mcp \
  --registry-url http://localhost:8803/mcp \
  --build-url http://localhost:8804/mcp \
  --federation-endpoint https://public.bc-code-atlas.example/mcp
```

## Validate User Story 1 (structural stub resolution)

1. Query the private instance's `bcatlas_get_node` (or a traversal tool) for
   the private test object that extends/references the known remote object.
2. Confirm the response for that cross-boundary edge's target is the real
   remote object's data (signature/location/edges), not an `external` stub
   -- and carries `_source`/`_remote_country`/`_remote_version` per
   data-model.md's `FederatedResult` shape.
3. Repeat with `--federation-endpoint` omitted entirely; confirm the same
   query returns today's stub behavior unchanged (SC-005).

## Validate User Story 2 (search fan-out)

1. Run `bcatlas_search` against the private instance with a query known to
   match content in both corpora.
2. Confirm results include items from both, each labeled with `_source`.
3. Stop the remote aggregator process, repeat the same query, and confirm
   the private instance still returns its own local results within a bounded
   time (R4), with a clear indication the remote source was unavailable.

## Validate User Story 3 (trust boundary)

1. Capture outbound HTTP traffic from the private instance's aggregator
   process during the above two runs (e.g. a local proxy/tcpdump).
2. Confirm no request body to the remote endpoint contains private-corpus
   source text or file paths -- only `global_id`/object-type lookups and the
   user's own literal search query text.
3. Confirm the remote/public instance's own configuration and code contain
   no reference to the private instance's address (one-directional trust).

## Expected outcome

All three user stories' acceptance scenarios (spec.md) pass; SC-001 through
SC-005 are each demonstrably true from the runs above, not just plausible by
design.
