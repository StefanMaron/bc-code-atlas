<!--
Sync Impact Report
- Version change: 1.0.0 → 1.1.0
- Modified principles: n/a
- Added sections: Core Principle VIII (Deploys Must Not Reset the Serving
  Index)
- Removed sections: none
- Templates requiring updates:
  - .specify/templates/plan-template.md ⚠ pending — its "Constitution Check"
    gate is generic; when the first real plan is generated, populate it with
    the concrete gates from Principles II-V below (build/serve separation,
    unbounded-scope check, measurement evidence, fork justification).
  - .specify/templates/spec-template.md ✅ no changes needed — generic
    user-story/requirements shape already fits this project.
  - .specify/templates/tasks-template.md ✅ no changes needed — generic
    phase/user-story shape already fits.
  - CLAUDE.md ⚠ pending — still describes the single-version local PoC
    mission and explicit non-goals that this constitution now supersedes;
    needs a full rewrite in a follow-up step, not part of this amendment.
  - README.md ⚠ pending — "This is a proof of concept" framing and the
    single-version Quick Start are now stale; follow-up step.
- Follow-up TODOs: none deferred — Principle VIII's claim was directly
  verified against the deployed code (not assumed) before being ratified;
  see the principle's Rationale for the exact code paths checked.
-->

# bc-code-atlas Constitution

## Core Principles

### I. Serve Like It's Remote

Every capability MUST be reachable the same way a remote community user will
reach it: as an MCP tool call over HTTP, never as an in-process function call
or a stdio-only shortcut, at any stage of development — including local
dev/test. Development and testing setups MUST run a separate client
session/config against live HTTP MCP servers, not read source or query
storage directly from the same process that's building the feature.

**Rationale**: integration and serving problems (auth, transport, tool
description clarity, response shaping, concurrency) only surface when the
real serving path is exercised. Anything built or verified in-process can
hide a defect that only appears once it's actually remote.

### II. Build and Serve Are Separate Resource Pools

Indexing/building (embedding, graph extraction) and query-serving MUST run
as physically separate concerns with different resource profiles: building
is GPU-bound, bursty, and queued with bounded concurrency; serving is
CPU-bound, multi-tenant, and horizontally scalable. A build MUST write to a
staging location and be promoted into the served location by an atomic
move/rename only after it succeeds — a writer and a reader MUST NOT hold
concurrent open connections to the same on-disk artifact. Query-time
embedding of the *incoming query text* runs wherever the serving process
runs (CPU is acceptable — the measured cost difference vs. GPU is
negligible for a single query) and MUST NOT require per-served-version
duplication of a loaded model; one shared embedding-capable serving process
(or a small pool) serves reads across all warm (country, version)
artifacts.

**Rationale**: cocoindex-code's own storage has no cross-process write
safety (no WAL — same-process reads/writes are coordinated by an in-process
RWLock, cross-process concurrent access to the same file is not safe beyond
a short busy-timeout). Confirmed by direct inspection of the vendored
source, not assumed. Conflating build and serve into one process forces
every served version to pay GPU/model cost it doesn't need and reintroduces
the exact cross-process file-safety hazard the tool wasn't designed for.

### III. Historical Versions Are Immutable — Only Tips Move

A specific (country, commit) build's content never changes once it has been
built. Only the single latest commit ("tip") of each country's branch is
ever a moving target that needs incremental catch-up. All caching,
eviction, and rebuild logic MUST rely on this: evicting a historical
version's warm artifacts is always safe and always cheap to reverse
(rebuild from the same immutable source), while a country's tip is the only
case requiring a live refresh path.

**Rationale**: this is what makes "unlimited versions, bounded only by
hardware" tractable at all — eviction has no correctness risk and no
un-rebuildable state, so the system can be aggressive about reclaiming disk
without a data-loss story.

### IV. Unbounded Scope, Bounded Residency

The system MUST NOT hardcode support for a single country or a single
version anywhere in its design. Any (country, version) pair that resolves
to a real commit on the upstream source-history repository MUST be
requestable and buildable on demand. What IS bounded, explicitly and by
configuration rather than by code structure, is how much stays warm at
once: an LRU/TTL eviction policy governs on-disk residency against real
disk/compute budgets. "Unbounded scope" and "keep everything ever
requested forever" are different claims — only the first is a requirement;
the second is the failure mode this principle exists to prevent.

**Rationale**: directly reflects the driving requirement of this
architecture phase — serve many concurrently-requested countries/versions,
limited by hardware, not by design — while keeping the earlier PoC's
learned lesson (an unscoped "index everything" attempt was tried once
before and abandoned) from repeating in a new form.

### V. Measure, Don't Assume

Any claim about upstream data shape, tool capability, or cost/feasibility
that will influence an architectural decision MUST be backed by a direct,
reproducible check against the actual repository, vendored source, or
running system — not by memory of how a similar tool "usually" works, and
not by a proxy metric that hasn't been verified to correlate with the real
question. When a cheap proxy (e.g. git commit-ancestry distance) is used to
estimate an expensive property (e.g. real content divergence between two
branches), the proxy MUST itself be validated against a direct measurement
before being trusted.

**Rationale**: this session directly disproved two of its own working
assumptions this way — GitHub's `ahead_by`/`behind_by` looked like it meant
two country branches were almost entirely different, until a real
tree-level diff showed ~85-90% byte-identical content; "cocoindex-code
storage is LMDB" (inherited from the original design brief) turned out to
be two SQLite databases on direct inspection of the vendored code. Both
corrections changed real decisions. Assumption-driven design would have
locked in the wrong architecture on both counts.

### VI. Minimal, Justified Forks

Vendored third-party tools (`cocoindex-code`, `tree-sitter-al`) are kept as
unmodified upstream checkouts wherever possible; new capability is built as
orchestration around their stock, documented behavior. A fork
(`graphify-al`) is permitted only when a concrete, reproducible bug or
missing capability is found against real project data, and every such
change MUST be documented (what, why, against which upstream commit) so it
stays re-evaluatable if upstream fixes it later.

**Rationale**: keeps the project upgradeable and keeps the surface area of
"code we must personally maintain" as small as the actual gaps require —
not smaller (silently working around real bugs) and not larger
(unnecessary local patches to code that already does the job).

### VII. Lean, Honest Agent-Facing Output

Every response returned over MCP to a calling agent MUST be shaped for that
agent's token economy: no incidental bloat (e.g. literal CRLF line endings,
excess floating-point precision) that carries no information; tool
descriptions and server instructions MUST accurately name what corpus/data
is actually being served, not what an earlier design brief said would be
served; tools whose broad/unscoped use is expensive or noisy (e.g. a blind
structural graph query with no starting symbol) MUST say so in their own
description and steer toward the cheaper path, rather than relying on the
calling agent to discover this by trial and error.

**Rationale**: this is a public multi-tenant service — token cost paid by
every caller compounds across the whole community, and a misleading tool
description wastes calls at scale in a way a single local user would never
notice.

### VIII. Deploys Must Not Reset the Serving Index

The shared search daemon's cold-start reindex (hours, not minutes — see
"Key facts already established" in `CLAUDE.md`) MUST NEVER be an implicit
side effect of a routine code deploy. A deploy is only permitted to restart
the search-serving process when: (a) the process resumes from its existing
on-disk state rather than reprocessing everything from zero, and (b) that
resumption has been verified against the actual deployed code, not assumed.
If a future change to `cocoindex-code`, its storage layout, or the deploy
path (env var overrides, path mappings, container/ephemeral-disk moves)
would break resumption, that change MUST NOT ship until an equivalent
persistence guarantee is restored — deploys are otherwise something that
happens constantly while operating this service (bug fixes, unrelated
features), and an accidental full reset on every one of them makes the
hosted VM approach itself infeasible, not just slow.

**Rationale**: directly verified by reading the deployed
`cocoindex-code` code (commit `7fe0e89`, identical on the VM and in this
repo at the time of verification) rather than inferred from one lucky
restart: `Project.create()`
(`tools/cocoindex-code/src/cocoindex_code/project.py`) opens
`cocoindex.db` (incremental-state tracking) and `target_sqlite.db` (vector
index) at a deterministic, disk-backed path
(`resolve_db_dir()`/`settings.py`, `project_root/.cocoindex_code/` with no
env override configured on the VM) using `mkdir(..., exist_ok=True)` — it
never creates fresh/empty stores on process start. `self._app.update()` is
cocoindex's own content-hash-based incremental engine: it diffs current
file content against what is already recorded in those persisted stores,
so files already indexed by a now-dead process come back as
`num_unchanged` (skipped) rather than being reprocessed. Live-measured
confirmation: after a routine `systemctl restart` on the hosted VM,
`num_unchanged` was already >10,000 immediately, not 0. This makes the
earlier working assumption baked into `chunker/chunking.py`'s
`CHUNKER_REGISTRY` comment and repeated through `CLAUDE.md` ("a fresh
daemon process cannot trust ANY prior state") obsolete and due for
correction there — that assumption was true of a *different* project root
per (country, version) build artifact (each of those genuinely has no
prior state the first time it's built), not of the one shared,
long-lived, single-path serving daemon this principle is about. The one
residual, not-yet-stress-tested risk: this reasoning assumes the on-disk
LMDB/SQLite state survives an *ungraceful* kill (e.g. the stall watchdog's
SIGKILL path) without corruption — both formats are designed to be
crash-safe, but that specific scenario hasn't been directly reproduced and
verified, only reasoned about from format guarantees.

## Technology & Data Constraints

- **Source of truth**: `StefanMaron/MSDyn365BC.Sandbox.Code.History` — one
  commit per build, per country branch (e.g. `w1-28`, `us-28`), commit
  message encodes the exact version string (e.g. `w1-28.2.50931.52151`).
  There are ~51 country codes × ~10-11 major versions each (546 branches
  observed). Branches share no git ancestry with each other even when
  their content is nearly identical — never infer content similarity from
  branch/commit ancestry (see Principle V).
- **Semantic layer**: `cocoindex-io/cocoindex-code`, kept as plain upstream
  (Principle VI). Storage is two SQLite databases per project directory
  (`target_sqlite.db` for vectors via `sqlite-vec`, `cocoindex.db` for its
  own incremental-state tracking) — not LMDB, correcting the original
  design brief. Isolation is per filesystem path (`project_root`); there is
  no native multi-branch/namespace/overlay capability in the open-source
  package — the "branch dedupe" feature mentioned in its README is an
  unspecified, undocumented capability of the paid hosted offering only,
  confirmed unusable here by direct source inspection.
- **Structural layer**: `StefanMaron/graphify-al` (fork of
  `ChristianHovenbitzer/graphify-al`, branch `bc-code-atlas-fixes`),
  AL-aware via `tree-sitter-al`. Cross-object call resolution is partial by
  design upstream (direct, statically-typed calls only); this is a known,
  documented limitation, not a bug to chase.
- **AL parsing**: `SShadowS/tree-sitter-al` everywhere an AL-aware parse is
  needed (chunking, graph extraction, on-demand exact-source lookup,
  symbol-level diffing). Never a hand-rolled AL parser.
- **Docs**: Microsoft's public BC docs (`dynamics365smb-docs`,
  `business-central/`) and the AL developer/compiler reference
  (`dynamics365smb-devitpro-pb`, `dev-itpro/developer/` — the successor to
  the design brief's original, now-defunct `dynamics365smb-devitpro`
  repo name) are indexed alongside code in the same semantic layer.

## Development Workflow

- **Isolate before scaling.** When a request implies a large or expensive
  change of scope (e.g. "support every country and every version"), decompose
  it into its independent axes and evaluate each on its own merits and cost
  before committing to build any of it — do not design the maximal version
  of a request when a smaller decomposition serves the same real need.
- **Validate the expensive assumption first, cheaply.** Before building
  infrastructure premised on a claim about cost, scale, or feasibility, find
  the cheapest real check that would falsify it (a single API call, a
  targeted diff, a timed one-off run) and run it before writing the
  infrastructure.
- **Prefer composing existing, verified primitives** (e.g. the tree-sitter
  based exact-source lookup already built for `get_signature` /
  `get_procedure_body` / `get_object_source`) over building parallel new
  ones for a closely related need (e.g. symbol-level version diffing) —
  reuse is the default, a new primitive needs its own justification.

## Governance

This constitution supersedes any conflicting guidance in `CLAUDE.md`,
`README.md`, or other instruction files; those files MUST be brought into
alignment with it rather than treated as an equally-authoritative source
when they disagree. `CLAUDE.md` and `README.md` still describing this
project as a single-version local proof-of-concept are known-stale as of
this ratification and are pending a rewrite (tracked in the Sync Impact
Report above), not a reflection of current intent.

Amendments require: the proposed change stated explicitly, its version-bump
classification (MAJOR for a backward-incompatible principle removal or
redefinition, MINOR for a new principle or materially expanded guidance,
PATCH for wording/clarification only), and propagation to any dependent
template or instruction file that referenced the changed material. Every
`/speckit-plan` run MUST include a Constitution Check gate that verifies
the proposed design against the Core Principles above, with any violation
explicitly justified in that plan's Complexity Tracking section rather than
silently accepted.

**Version**: 1.1.0 | **Ratified**: 2026-07-02 | **Last Amended**: 2026-08-03
