# Quickstart: validating the submodule-watch job

## Prerequisites

- `gh` CLI authenticated against `StefanMaron/bc-code-atlas` with a token that has
  `contents: write` / `pull-requests: write` (matches the workflow's own token scope).
- A local checkout of `bc-code-atlas` with `data/` submodules NOT necessarily
  initialized (the job is designed not to need them -- see contracts/detection-job.md).

## Local dry run

```bash
# From repo root, no submodule checkout required:
python scripts/check-submodule-updates.py --dry-run
```

Expected: for each of the three watched submodules, one line reporting
`pinned=<sha> upstream=<sha> action=<none|would-open-pr>`. No branches, commits, or PRs
are created in `--dry-run` mode.

## End-to-end validation (real PR)

1. Manually trigger the workflow: `gh workflow run submodule-watch.yml`.
2. Confirm outcome per real state:
   - **If any watched submodule is behind its upstream tip** (expected for at least one
     of the three most days, given real upstream drift already observed this session for
     `w1-28`): a new PR appears, titled `[auto] Bump <path> to <short-sha>`, authored by
     `github-actions[bot]`, changing only that submodule's gitlink.
   - **If all three are already current**: the workflow run completes with no PR opened
     (User Story 1 acceptance scenario 2).
3. Confirm the opened PR triggers the existing `ci.yml` checks exactly like a hand-made
   bump PR (User Story 3 acceptance scenario 1) -- watch the PR's checks tab.
4. Re-run the workflow (`gh workflow run submodule-watch.yml`) while the PR from step 2
   is still open: confirm no duplicate PR is opened for the same target commit (FR-004).
5. Close the PR from step 2 without merging, then re-run the workflow: confirm a new PR
   is proposed again (Edge Case: rejected PR must not permanently skip that submodule).
6. Merge a real bump PR once its CI is green (same manual step as every other submodule
   bump in this repo) and confirm `deploy.yml` picks it up exactly as it would for a
   hand-authored bump PR -- no new deploy path was introduced by this feature.

## What this quickstart deliberately does not cover

Implementation of `scripts/check-submodule-updates.py` itself and its unit/integration
tests belong to `/speckit-tasks` + `/speckit-implement`, not this validation guide.
