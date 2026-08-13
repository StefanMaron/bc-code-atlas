# Research: Automated Default-Corpus Update Detection

## Decision: Scheduled GitHub Actions cron, not a webhook

**Decision**: Detection runs on a scheduled GitHub Actions workflow (`on: schedule`, plus
`workflow_dispatch` for manual runs), not a webhook.

**Rationale**: None of the three watched upstream repositories
(`StefanMaron/MSDyn365BC.Sandbox.Code.History`, `MicrosoftDocs/dynamics365smb-docs`,
`MicrosoftDocs/dynamics365smb-devitpro-pb`) are ours to configure outbound webhooks on --
two are Microsoft's, and even the sandbox-history mirror has no existing webhook
infrastructure pointed at this repo. A real webhook receiver would require standing up a
new public HTTP endpoint plus auth on our side purely to receive a ping we could get by
just asking upstream directly -- new infrastructure the spec's own Assumptions section
says isn't needed ("this feature's scope ends at opening a mergeable pull request").
A daily schedule is simple, needs no new infra, and matches spec.md's "reasonable default
cadence... not user-facing" assumption.

**Alternatives considered**: A real webhook (rejected -- requires infra we don't control
upstream and aren't building here); polling from the always-warm serving VM itself
(rejected -- conflates a CI/repo-maintenance concern with the serving daemon's own
process, and the VM has no GitHub write credentials today).

## Decision: Detect upstream advancement via `git ls-remote`, not a full mirror clone

**Decision**: For each watched submodule, resolve the upstream branch tip with a single
`git ls-remote <url> <branch>` call.

**Rationale**: Unlike `registry/registry/git_ops.py`'s `list_commits` (which needs a full
blobless history walk to resolve loose version specs like `28.1` against commit
messages), this feature only ever needs one fact per submodule: "has the branch tip moved
past what's pinned." `ls-remote` is a single stateless network call, real measurement per
constitution Principle V, and avoids the cost of maintaining a local mirror for docs
repos that `registry/`'s machinery was never built for. `registry/git_ops.py`'s mirror
machinery is reused conceptually (same "ask upstream directly, don't infer" posture) but
not imported -- pulling in `registry` as a dependency for a three-line git call would
invert the actual complexity relationship.

**Alternatives considered**: Reusing `registry.git_ops.list_commits` for all three
submodules (rejected -- it's built for `w1-28`'s version-spec resolution across the
`StefanMaron/...History` repo specifically; the two docs repos have no equivalent
build-per-commit convention and don't need history walking, only a tip check).

## Decision: Read the pinned commit via `git ls-tree`, never check out the submodule

**Decision**: The currently pinned commit for a watched submodule is read with
`git ls-tree HEAD -- <path>`, which returns the gitlink (`160000` mode) entry recorded in
the parent repository's tree -- no submodule init/checkout required.

**Rationale**: `data/w1-28-src`, `data/docs`, and `data/docs-devitpro` are large (the
existing CI workflow already avoids checking any of them out, per `ci.yml`'s
`submodules: false` and its comment about "the multi-GB `data/` corpora"). Reading the
gitlink sha directly is the same trick the parent repo already uses to track a
submodule's pin without materializing its content.

## Decision: Bump the pointer with `git update-index --cacheinfo`, not `git submodule update`

**Decision**: To change a submodule's pinned commit, run
`git update-index --cacheinfo 160000,<new_sha>,<path>` against the checked-out parent
repo (no submodule content needed), then commit that one change.

**Rationale**: This directly rewrites the gitlink tree entry -- functionally identical to
`cd <path> && git checkout <new_sha> && cd .. && git add <path>`, but doesn't require
fetching the submodule's content at all, consistent with the same minimal-checkout
posture CI already uses elsewhere. This keeps the job cheap and fast (no multi-GB clone)
even though it runs daily.

## Decision: Duplicate-PR guard via branch-name convention, not text search

**Decision**: Each proposed bump uses a deterministic branch name,
`bot/bump-<submodule-slug>-<short-sha>` (e.g. `bot/bump-w1-28-src-a1b2c3d`). Before
opening a PR, the job checks whether that exact branch (and an open PR from it) already
exists via `gh pr list --head <branch-name> --state open`.

**Rationale**: A deterministic name keyed on (submodule, target commit) makes "does this
exact bump already have an open PR" an exact-match lookup rather than a fuzzy title/body
text search -- more robust per FR-004, and trivially satisfies "same or newer commit
after a rejected PR was closed" (Edge Cases) since a new target commit produces a new
branch name automatically, never colliding with the closed one.

**Alternatives considered**: Searching PR titles/bodies for a marker string (rejected --
fragile against title edits and against GitHub's search-index latency); tracking state in
a new file/database in the repo (rejected -- adds persistent state to maintain for
something GitHub's own open-PR list already tells us for free).

## Decision: Never call `gh pr merge`; scope workflow permissions accordingly

**Decision**: The workflow's `permissions:` block grants only `contents: write` and
`pull-requests: write`. No code path in the script calls a merge operation.

**Rationale**: Directly satisfies FR-005/User Story 3 -- the absence of merge permission
at the workflow-token level, not just the absence of a merge call in the script, is the
actual safety gate (defense in depth: even a bug in the script couldn't merge, since the
token it runs with is never granted that scope).

## Decision: Identify automated PRs via bot authorship + a title/body marker

**Decision**: PRs are opened using the workflow's default `GITHUB_TOKEN` (so they're
authored by `github-actions[bot]`), with titles prefixed `[auto]` and a PR body noting
which workflow run produced them, linking back to the run.

**Rationale**: Satisfies FR-009 two ways at once (bot authorship is visible in every
GitHub PR list view without opening the PR, matching SC-004; the `[auto]` prefix and body
note give a second, redundant signal in case authorship alone isn't enough context for a
reviewer skimming a mixed list).

## Decision: History-rewrite tolerance is free, by construction

**Decision**: No ancestor/fast-forward check is performed anywhere in this design --
`ls-remote`'s reported tip is proposed as-is, and the pinned-commit bump doesn't care
whether the new sha is a descendant of the old one.

**Rationale**: Satisfies FR-007/Edge Case 1 (upstream force-push) without any special-case
code, because nothing in this design ever asked "is X an ancestor of Y" in the first
place -- unlike `registry/git_ops.py`'s `log_for_path`, which genuinely needs ancestry to
walk a commit range and had to be built to tolerate rewrites explicitly. This feature has
no equivalent need, so there's no equivalent risk.

## Decision: Per-submodule failure isolation via independent steps

**Decision**: Each watched submodule's check-and-propose logic runs as an independent
unit (either separate script invocations or an explicit per-item try/except loop in one
script), so a network failure reaching one upstream mirror logs a warning and skips only
that submodule, never aborting the run for the other two.

**Rationale**: Directly satisfies FR-008/Edge Case 2.
