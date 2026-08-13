# Contract: Submodule-watch detection job

This feature's only external interface is the GitHub Actions workflow it adds and the
pull requests that workflow produces -- there is no library API or MCP tool (constitution
Principle I doesn't apply here: this is a CI/repo-maintenance job, not a capability served
to BC/AL developer agents).

## Workflow trigger contract

- **Schedule**: `on.schedule` (cron), cadence per research.md (daily default, not
  user-facing).
- **Manual**: `on.workflow_dispatch`, no required inputs -- lets a maintainer force a
  check without waiting for the schedule (useful for validating this feature after
  merge, and for the "detect and propose again after a rejected PR was closed" edge
  case).
- **Permissions**: `contents: write`, `pull-requests: write` only. No `contents: admin`,
  no merge-capable scope of any kind (FR-005).

## Per-submodule check contract

For each of the three watched submodules (data-model.md), the job:

1. Resolves `pinned_sha` (`git ls-tree HEAD -- <path>`) and `upstream_tip_sha`
   (`git ls-remote <upstream_url> <branch>`).
2. If unreachable (network failure resolving `upstream_tip_sha`): log a warning, skip
   this submodule only, continue to the next (FR-008). Never raises out of the whole job.
3. If `upstream_tip_sha == pinned_sha`: no action (FR-001 acceptance scenario 2).
4. If they differ: compute `head_branch = bot/bump-<submodule-slug>-<short(upstream_tip_sha)>`.
   - If an open PR already exists with that exact head branch: no action (FR-004).
   - Otherwise: create the branch, `git update-index --cacheinfo 160000,<upstream_tip_sha>,<path>`,
     commit, push, open a PR via `gh pr create` with the title/body shape in
     data-model.md (FR-002, FR-009).

## Non-goals of this contract

- Never invokes `gh pr merge` or any equivalent (FR-005).
- Never touches more than one submodule's gitlink in a single commit/PR (FR-003).
- Never triggers a build or deploy directly -- the existing `ci.yml` (triggered by the PR)
  and `deploy.yml` (triggered by a subsequent merge to `master`) pick this PR up
  unchanged, exactly as any hand-made bump PR would (spec.md Assumptions).
