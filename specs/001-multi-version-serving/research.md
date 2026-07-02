# Phase 0 Research: Multi-Country, Multi-Version Serving

No `NEEDS CLARIFICATION` markers were left in the Technical Context — the technical approach
was already established through direct measurement and discussion prior to this spec (see
`.specify/memory/constitution.md` and `CLAUDE.md`). This document consolidates those prior
decisions into the Decision/Rationale/Alternatives format, plus a small number of net-new
implementation-detail decisions needed to turn the approach into a concrete plan.

## Decision: version discovery/resolution via direct git operations, no new database

**Rationale**: git itself already is the authoritative, always-current source of truth
(branches = countries×versions, commits = builds, commit message = exact version string,
confirmed directly against the real repo this session: 546 branches, ~51 countries, one
commit per build). A separate catalog/database would need to be kept in sync with upstream
and could drift; querying git directly (`ls-remote`, `log`, `show`) never can.

**Alternatives considered**: a periodically-refreshed local database mirroring
branch/commit metadata — rejected for this feature; adds a sync/staleness failure mode with
no offsetting benefit at this data volume (`ls-remote` against 546 branches is a single fast
network call, already measured as near-instant this session).

## Decision: on-demand historical blob/commit fetch via scoped shallow fetches into a shared mirror

**Rationale**: today's submodules are `shallow = true` at depth 1, tip-only. A full clone of
even one country's full history (thousands of commits × large tree) is unnecessary and slow;
`git fetch origin <sha> --depth 1` (proven working this session against the real repo)
fetches exactly the one commit and its tree, nothing else. A single shared local mirror
(rather than one clone per request) avoids redundant network fetches for the same commit
requested by multiple concurrent callers.

**Alternatives considered**: GitHub REST/compare API for content — rejected as the primary
mechanism; it was useful for quick measurement this session but its `files` list truncates at
300 (confirmed this session against the `w1-28`/`us-28` comparison) and it isn't a general
substitute for real blob content needed by the diff/history tools.

## Decision: symbol-scoped diff by independent per-version extraction, not a git line-diff

**Rationale**: line numbers shift between versions, so diffing byte ranges anchored to line
numbers from one version against another is wrong. Fetching each version's file blob
independently, parsing each with `tree-sitter-al`, locating the target symbol by name in
each, and diffing the two extracted texts is correct regardless of where the symbol moved
within the file.

**Alternatives considered**: git's own line-based diff scoped to a file — kept as the
file-scope option (FR-006) since it's the right tool when the caller wants "what changed in
this file," but it is not used for symbol scope, where it would misattribute unrelated
nearby changes to the target symbol or miss the symbol's own change if line numbers shifted
enough to dodge the requested range.

## Decision: multi-step symbol history via `git log` scoped to the containing file, filtered by per-commit symbol re-extraction

**Rationale**: `git log -- <path>` gives every commit that touched the file, but per FR-008
only commits that changed the *symbol's own resolved text* should appear in the chain — a
commit touching an unrelated procedure in the same file must not appear. Re-running the same
extraction-and-compare used for the two-point diff at each touching commit, and keeping only
steps where the extracted text actually differs from the previous kept step, satisfies this
without needing any new indexing.

**Alternatives considered**: none pursued in depth — this is a direct, minimal composition
of two already-decided primitives (git log scoping + symbol extraction), not requiring
independent research.

## Decision: build/serve split — staging + atomic promote, bounded queue, shared embedding-capable serving process

**Rationale**: direct inspection of the vendored `cocoindex-code` source this session found
no WAL mode; same-process concurrent access is safe (an internal fair RWLock), cross-process
concurrent access to the same SQLite file is not guaranteed safe beyond a short busy-timeout.
Staging + atomic rename promotion means a serving process only ever opens a file that is not
concurrently being written by any build process — this fully avoids the hazard without
needing any change to cocoindex-code itself (constitution Principle VI). Query-time embedding
was also confirmed this session to run on every search call with no caching
(`query_codebase()` unconditionally embeds) — since GPU vs. CPU query-embedding latency is
nearly identical (already benchmarked, ~8ms vs ~10ms), the serving process can run this on
CPU, meaning one shared serving process/pool can serve reads for every warm (country,
version) without needing its own loaded model per version.

**Alternatives considered**: one OS process per warm version (today's actual runtime
behavior) — rejected as the target design because it multiplies per-version OS/model-loading
overhead and does not scale to "unbounded servable pairs, hardware-limited" (constitution
Principle IV); kept conceptually as "the currently-running w1-28 setup is one instance of the
new multi-tenant pattern with residency of exactly one," not a parallel code path.

## Decision: incremental builds via clone-nearest-warm-sibling + patch-git-diffed-files + stock `ccc index`

**Rationale**: `cocoindex-code`'s isolation unit is the filesystem path
(`ProjectDaemon.get_project(project_root)`), and its incremental-state tracking
(`cocoindex.db`) lives inside that same project directory — confirmed by direct source
inspection this session. Cloning an already-built project directory (source tree + its
`.cocoindex_code` state) to a new path, then overwriting only the files that actually differ
(from a real `git diff --name-only` between the two resolved commits) before running `ccc
index` again, lets cocoindex's own stock change detection do the re-embedding work, without
any fork. This is expected to be cheap for same-country version hops (measured this session:
one real 99-build/full-minor-version span touched 269 of the corpus's `.al` files, ~1%) and
plausible for cross-country pairs too, since real content overlap was separately measured at
~85-90% between two unrelated-ancestry country branches (`w1-28` vs `us-28`) — but the actual
wall-clock saving for a real incremental run has not yet been measured and MUST be validated
during implementation (constitution Principle V), not assumed from the file-count figure
alone.

**Alternatives considered**: full cold rebuild per version — rejected as the default path
(defeats the purpose, ignores measured overlap); a bespoke diff-aware embedding cache
bypassing cocoindex-code entirely — rejected, would require forking/reimplementing
cocoindex's own incremental logic, violating constitution Principle VI without a demonstrated
need.

## Decision: LRU/TTL eviction over warm directories under a configured disk budget

**Rationale**: constitution Principle III (historical versions are immutable) makes eviction
safe by construction — a reclaimed (country, version) is always re-buildable from the same
immutable source, so there is no data-loss risk to weigh against reclaiming space. A simple
last-access-timestamp-per-directory scheme with a periodic or on-demand sweep is sufficient;
no need for a more sophisticated cache-replacement policy at this stage.

**Alternatives considered**: no eviction (keep everything ever built) — explicitly rejected,
this is exactly the "bazooka" failure mode identified earlier this session and excluded by
constitution Principle IV.

## Decision: request coalescing for concurrent duplicate build requests

**Rationale**: FR-017 requires this explicitly (two concurrent requests for the same
(country, version) must not duplicate work). A per-(country, version) in-flight marker in the
build queue, with subsequent requests for the same key attaching to the existing in-flight
result rather than starting a new build, satisfies this with a small, well-understood
pattern (equivalent to a "singleflight"/promise-memoization pattern).

**Alternatives considered**: none needed — this is a standard, low-risk pattern with no
project-specific wrinkle requiring deeper research.
