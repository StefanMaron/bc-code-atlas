# IDEAS.md

Running backlog of not-yet-decided architectural directions. Nothing here is
committed or spec'd -- this is a holding pen for ideas to revisit and
elaborate later, most likely via `/speckit-specify` once one is actually
decided on. See `specs/001-multi-version-serving/` for the currently
in-flight, decided work; this file is deliberately outside that process
until something here graduates into it.

## Idea: a derived layer over the History repo, not the repo itself

Captured 2026-07-02, from a conversation about disk/memory scaling for
serving many (country, version) pairs at once.

The upstream source, `StefanMaron/MSDyn365BC.Sandbox.Code.History`, is
**owned by the project owner** (not a true third-party dependency) -- so
unlike `cocoindex-code` (constitution Principle VI: must stay unforked),
there's no hard constraint against reshaping how its content is stored or
derived from. The one real constraint: no breaking changes to the repo
itself (its branch-per-country/version git history is presumably valuable
as-is, and other tooling may depend on its current shape).

The idea: build an **additive, derived layer on top of it** -- not a
replacement -- that re-expresses its content in a form our indexing
pipeline (`cocoindex-code`, `graphify-al`) can work with more efficiently,
without duplicating full file trees per (country, version) the way
`data/warm/` does today (measured this session: ~3GB per warm entry, zero
dedup across countries/versions despite ~85-90% real cross-country content
overlap and ~1% same-country version-hop file-change rate).

Note one nuance surfaced in discussion: git itself already deduplicates
identical blobs by content hash regardless of branch ancestry, so the
*upstream repo's own* `.git` object storage is plausibly already
reasonably efficient at the raw-blob level (not independently confirmed by
measurement -- would need a real check of the mirror's `.git` size vs. a
naive per-branch-checkout estimate). The clearly-measured inefficiency is
in **our own** `data/warm/` served copies, which is the layer this idea
targets.

## Idea: one unified service instead of N siloed (country, version) pairs

The more ambitious version of the above: instead of a caller having to
resolve/request/poll/switch between individual (country, version) pairs
(today's model), serve **one queryable knowledge base spanning every
country and every version at once** -- a client just searches or traces
the graph and sees relevant results across the whole history in one shot,
with no version-switching step at all.

Key mechanism that could make this size-tractable rather than a ~500x
blowup (51 countries x ~10-11 majors x many builds each): deduplicate at
the **embedding/chunk layer** by content hash, not just at the disk-copy
layer. Compute one embedding per unique file-content variant, tag it with
every (country, version) it actually appears in. Given the real overlap
numbers already measured this session, the number of *truly distinct*
content variants across all of BC's history is plausibly a small multiple
of one version's corpus, not the naive full cross-product. A search query
then returns hits annotated with their applicable version/country ranges,
with no upfront resolve-a-version step.

**This is judged (in discussion, not yet verified) to be the easier half.**
The **structural graph is the hard half**: calls/subscribes/extends edges
are inherently version-sensitive in ways a node's mere declaration isn't
-- an edge can appear or disappear between versions even when neither
endpoint's own file changed (e.g. an event subscriber added elsewhere).
Content-hash dedup handles a node's declaration fine, but a unified graph
would need every *edge* tagged with the version-set it's valid in, and
graph algorithms already in use (community detection, shortest-path, god
nodes) would need to reason about that dimension too. The existing
symbol-history feature (US2, already built) does a narrow, targeted
version of exactly this for one symbol at a time -- generalizing that to
the whole graph is a different scale of problem, not a simple extension.

## Open question the owner raised, unresolved: is this actually necessary?

Explicitly flagged as unresolved, not a rhetorical question: **is the
current per-(country, version) siloed design, scaled with more hardware,
simply the better and cheaper answer overall** compared to the
unification effort above? Worth weighing before committing engineering
time to either idea above:

- What's the actual expected usage pattern -- how many distinct
  (country, version) pairs would realistically be *concurrently* hot at
  once for a real community of BC/AL developers? (Today: 0 evidence
  either way, only synthetic/test traffic so far.)
- The measured per-pair costs (disk ~3GB/pair, graph-cache RAM ~1.5GB per
  resident large-country graph) may simply be affordable to scale
  horizontally/vertically with commodity hardware, without needing either
  of the ideas above.
- Both ideas above are real engineering investments with real risk;
  "throw a bit more hardware at the current design" has near-zero
  engineering risk by comparison. The bar for choosing unification (or
  even just the derived-storage-layer idea) over scaling as-is should be
  a concrete, measured usage pattern that the current design genuinely
  can't serve affordably -- not assumed ahead of time.

## Open question, newly raised and not yet touched at all: daily-rebuild / freshness cost

Not yet analyzed in any form. The real problem: upstream publishes new
builds/hotfixes on some real cadence. To keep served data current for
actively-requested (country, version) pairs, *something* has to periodically
rebuild them against the latest commits on their branch. How expensive is
that in aggregate, once more than a couple of (country, version) pairs are
actively kept fresh?

Real numbers already measured this session, worth carrying into that
analysis directly rather than re-measuring blind:

- Cold build (`w1-28.0`, full corpus): ~20.6 min (1237s) end-to-end, most
  of it the GPU-bound embedding step.
- Incremental hop (`w1-28.0` -> `w1-28.1`, real minor-version bump, 1231 of
  19,276 files changed = 93.6% unchanged): ~11 min 8s (668s) total, split
  as ~107s embedding (a ~92% reduction vs. cold, incrementality working as
  intended) but **~561s graphify** (barely different from a full cold
  extraction, because `graphify-al`'s clustering/labeling/export pipeline
  isn't incremental regardless of diff size).
- After wiring graphify's existing-but-unexposed `changed_paths` mode
  through a new `--changed-paths-file` CLI flag: graphify dropped to
  ~433s, total incremental to ~507s -- a real but modest ~24% win, because
  `changed_paths` mode only skips re-running AST extraction on unchanged
  files; it still re-runs full community clustering, labeling, and a full
  JSON export/serialization (parsing/rewriting the entire ~450MB+
  `graph.json`) every single time, regardless of how small the source
  diff is.

So: **graphify is the real bottleneck for keeping many versions fresh on
a recurring cadence**, not the embedding step (which incrementality
already handles well). If N versions need daily refresh and graphify
costs ~7-9 minutes per refresh regardless of diff size, N versions refreshed
daily costs roughly `N x ~8 min` of GPU/CPU time per day just for graph
upkeep, before even counting embedding time or a realistic hotfix cadence
faster than daily. Needs real design thought once (or if) this becomes a
live concern:

- Could hotfix-only refreshes (small diffs, frequent) be batched/debounced
  rather than triggering a full graphify re-cluster+export every time?
- Is a cheaper "just re-run AST extraction and skip re-clustering/re-export
  until some threshold of accumulated change" mode worth building into
  `graphify-al` (still our own fork, so buildable) -- trading some graph
  staleness (communities/labels drift slightly behind latest extraction)
  for much cheaper frequent refreshes?
- How does this interact with the "one unified service" idea above --
  if graph edges are tagged by version-range rather than rebuilt whole
  per version, does a hotfix only need to update the tags/diff on
  existing edges rather than a full re-cluster? (Speculative -- not
  reasoned through yet.)

## Idea: extend graphify-al's node model to field/control/data-item granularity

Captured 2026-07-03, from a live gap found while writing tool descriptions:
an agent tried `bcatlas_search` to find `field(70; Comment; ...)` inside a
known table (`GenJournalLine.Table.al`) -- the wrong tool for a
known-location exact lookup, but checking the right tool
(`bcatlas_get_object_source`/`bcatlas_get_signature`) surfaced a real gap
underneath: there is no node for the field at all. Direct inspection of the
live warm graph (grep across `GenJournalLine`'s ~1,157 matching nodes)
confirmed today's `graphify-al` fork only extracts object-level and
procedure/trigger-level nodes -- table field declarations, page/report
controls, and report/query data items have no node of their own, so the
narrowest possible lookup for one is "pull the whole object source and read
the line out of it" via `bcatlas_get_object_source`.

The proposed extension: teach `graphify-al`'s AL extraction to also emit
nodes for table fields (id, name, type, and per-field triggers like
`OnValidate`/`OnLookup`, which today apparently collapse into
oddly-generic-looking node IDs shared across a table rather than being
disambiguated per field) and for page/report controls and data items. This
would make `bcatlas_get_signature`/`bcatlas_get_object_source` able to
target one field or control directly, and would let the structural graph
represent field-level triggers as first-class nodes with their own real
edges (a field's `OnValidate` can call other procedures today, but that
edge -- if captured at all -- is presumably attributed to the table object
rather than the specific field).

This qualifies as a legitimate fork change under constitution Principle VI
(missing capability found against real project data, not upstream's
partial-call-resolution limitation that's out of scope) -- but it's a real
feature, not a quick patch, so it should go through `/speckit-specify`
rather than being hacked into `extract.py` ad hoc. Open questions before
it's spec-ready:

- **ID/uniqueness scheme**: field nodes need IDs disambiguated per
  containing table (today's per-table `OnValidate()` node ID pattern looks
  like it may already collide across fields within the same table --
  worth confirming by direct inspection before assuming the schema even
  needs to change vs. just needs querying differently).
- **Edge semantics**: do field/control triggers get their own
  call/subscribes/extends edges (more precise, more nodes/edges to
  maintain), or stay attributed to the parent object (cheaper, less
  precise)?
- **Rebuild cost**: this is a structural-extraction schema change, so
  every already-built (country, version) pair's graph would need a full
  graphify re-run to backfill the new node types -- not an incremental
  add. Should be weighed against the freshness-cost findings above (graphify
  is already the dominant cost of keeping any one version fresh, before
  adding more node types to extract).
- **Actual demand**: today's only evidence this gap matters is one
  synthetic tool-description exercise, not a real, repeated caller need --
  worth confirming this is worth the schema/rebuild cost before spec'ing
  it, per the "measure, don't assume" principle.

## Idea: zero-downtime restart for the aggregator and its backends

Captured 2026-07-03, from a live incident with a real external tester
through the Cloudflare Tunnel deployment (see `CLOUDFLARE_TUNNEL.md`'s
"Known limitation" section for the full writeup).

Every one of `scripts/start-*.sh` (search, graph, registry, build,
aggregator) is a plain kill-and-relaunch -- there's no graceful
drain/handoff. Confirmed live: restarting the aggregator process at
`10:07:13 UTC` lined up second-for-second with a burst of `unexpected EOF`
errors in the Cloudflare Tunnel container's log against
`originService=http://localhost:8800`, and the real tester connected at
that moment saw a client-side crash and had to manually restart their own
MCP client to recover. The tunnel container itself self-healed (dialed a
fresh connection for the next request with zero action needed), but
nothing on our side protected whoever was mid-request at the exact
instant of the restart.

Possible directions, not decided:
- Graceful SIGTERM handling in each server so an in-flight request
  finishes before the process actually exits, instead of the connection
  just dying.
- A blue-green swap: bring up the new process on a temporary port, health
  check it, then only kill the old one and rebind the real port once the
  new one is confirmed healthy -- avoids even a few-hundred-ms gap where
  the port has no listener at all.
- Simplest non-engineering mitigation: just avoid restarting during a
  known-active testing window -- doesn't fix the underlying gap but costs
  nothing to do today.

Worth weighing against how often these processes actually get restarted
in practice (this session restarted the aggregator/search/graph several
times while iterating on bug fixes -- restarts are not rare during active
development, even if they'll likely be rarer once things stabilize).

## Idea: automate default-corpus promotion when upstream publishes a new build

Captured 2026-07-03, from a live incident: checking whether the latest w1
commit was warm surfaced that upstream had force-pushed (rewrote history
on) both `w1-27` and `w1-28`, which also happened to ship a real one-line
hotfix (`GenJnlPostLine.Codeunit.al`, `CreateGLEntryBalAcc`) plus its test.
`registry/git_ops.py`'s branch fetch was fixed same-day to force-update
(non-fast-forward-safe) so this class of upstream rewrite no longer breaks
resolution outright -- but that fix only keeps *resolution* working; it
does nothing to keep the *served default corpus* current.

Today the default corpus (`data/w1-28-src` submodule pin +
`data/.cocoindex_code`, always-warm fallback for every search/graph tool)
is a fixed pin, manually advanced via the original Quick Start steps.
Meanwhile `bcatlas_request_version` can build any (country, version) pair
on demand into `data/warm/`, but nothing promotes a build to *become* the
default, and nothing evicts the previous default once a newer one is in
place.

The proposed automated path: detect a new commit on the tracked branch for
the current major (`w1-28` now, `w1-29` once that's the live major) ->
build it (reusing the existing incremental pipeline against the
already-warm prior default as the sibling) -> once ready, promote it to
be the new default -> decommission (evict) the prior default immediately,
since a superseded hotfix build has no standing reason to stay warm
alongside its replacement.

This is the same gap CLAUDE.md's "Known open items" already calls out as
future work ("Reindex-webhook wiring into the sandbox-history repo's own
GitHub Actions") -- this entry just gives it a fuller shape now that the
build/serve split it depends on actually exists and a real live incident
motivated it. Real open design questions before this is spec-ready:

- **Trigger mechanism**: poll the registry for the tracked branch's tip on
  some cadence, vs. a webhook from the sandbox-history repo's own GitHub
  Actions (mentioned as the original plan) pushing a build request
  directly. A webhook avoids poll latency/cost but is an external
  integration; polling is simpler but its cadence directly trades off
  against the freshness-cost analysis above (graphify's ~7-9 min/refresh
  floor, not the embedding step).
- **What "current default" tracking looks like**: a config value, a file,
  or derived from the warm cache's own metadata -- needs to generalize
  across countries/majors (w1-28 today, w1-29 later, plus every other
  country this service eventually serves) rather than being a single
  hardcoded pin the way `data/w1-28-src` is today.
- **Promote mechanics**: can the search/graph servers hot-reload new
  default data, or does promotion require a restart (a real availability
  gap during the swap, worth avoiding if possible -- see constitution
  Principle II on staging/atomic-promote, which this would need to extend
  to the default-corpus paths specifically, not just `data/warm/`).
- **Decommission timing**: "right away" as stated, but worth deciding
  whether that's truly immediate on promote or a short grace window (in
  case an in-flight query against the old default is still running when
  the swap happens).
