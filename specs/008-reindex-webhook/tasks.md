---

description: "Task list for feature implementation"
---

# Tasks: Automated Default-Corpus Update Detection

**Input**: Design documents from `/specs/008-reindex-webhook/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/detection-job.md, quickstart.md

**Tests**: Included — spec.md's Success Criteria are only verifiable with real behavior
checks, and this repo's existing convention (`registry/tests`) is to pair pure-logic unit
tests with a real, network-marked integration test rather than mock upstream git state.

**Organization**: Tasks are grouped by user story (spec.md: US1 = detect code P1, US2 =
detect docs P2, US3 = safety gate P1) to enable independent implementation and testing of
each.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)

## Path Conventions

Per plan.md's Structure Decision: `scripts/check_submodule_updates.py` (single-file CLI
module, stdlib only) + `scripts/tests/test_check_submodule_updates.py` +
`.github/workflows/submodule-watch.yml`.

---

## Phase 1: Setup

**Purpose**: Project initialization for the new `scripts/` module

- [X] T001 Create `scripts/__init__.py` and `scripts/tests/__init__.py` so
      `scripts/tests/test_check_submodule_updates.py` can import
      `scripts.check_submodule_updates` (repository root has no `scripts/` package
      today — `scripts/deploy-vm.sh`/`scripts/wait-for-search-ready.py` are untested
      shell/one-off glue, per plan.md's Structure Decision)
- [X] T002 Add a minimal `scripts/pyproject.toml` (or extend the repo-root one if one
      exists — check first) declaring pytest as a dev dependency and registering the
      `network` marker, matching `registry/pyproject.toml`'s
      `[tool.pytest.ini_options]` convention so `pytest -m "not network"` keeps working
      repo-wide
- [X] T003 [P] Add a `test (scripts)` matrix entry to `.github/workflows/ci.yml`
      alongside the existing `registry`/`build`/`tools/graphify-al` entries, running
      `pytest scripts/tests -q` (per CI's existing per-project `uv sync` +
      `pytest -m "not network"`-style pattern — confirm which convention `ci.yml`
      matrix currently uses and follow it exactly)

**Checkpoint**: `scripts/` is a real, testable Python package wired into CI

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The generic per-submodule detection/proposal logic every user story
depends on — a "watched submodule" (data-model.md) is handled identically regardless of
which of the three it is, so this core is built once, not per-story.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T004 Implement `WatchedSubmodule` config loading in
      `scripts/check_submodule_updates.py`: a fixed list of the three watched entries
      (path, `upstream_url`, `branch`) per data-model.md's "Watched submodule" table —
      hardcoded in the script, NOT derived from `.gitmodules` wholesale (spec.md
      Assumptions explicitly excludes `tools/graphify-al` and other tooling submodules)
- [X] T005 [P] Implement `get_pinned_sha(path) -> str` in
      `scripts/check_submodule_updates.py` via `git ls-tree HEAD -- <path>` (research.md
      "Read the pinned commit via git ls-tree" decision — no submodule checkout)
- [X] T006 [P] Implement `get_upstream_tip_sha(url, branch) -> str` in
      `scripts/check_submodule_updates.py` via `git ls-remote <url> <branch>`
      (research.md "Detect upstream advancement via git ls-remote" decision)
- [X] T007 Implement `derive_branch_name(submodule_path, target_sha) -> str` in
      `scripts/check_submodule_updates.py` producing
      `bot/bump-<submodule-slug>-<short-sha>` (research.md "Duplicate-PR guard via
      branch-name convention" decision; contracts/detection-job.md step 4)
- [X] T008 Implement `plan_action(pinned_sha, upstream_tip_sha, open_pr_exists) ->
      Action` pure-decision function in `scripts/check_submodule_updates.py` returning
      one of `NONE` / `OPEN_PR`, matching contracts/detection-job.md's per-submodule
      check contract steps 3-4 exactly (kept side-effect-free and unit-testable
      separately from the git/gh-calling code around it)
- [X] T009 [P] Unit tests for `plan_action` in
      `scripts/tests/test_check_submodule_updates.py`: same-sha → `NONE`; different-sha
      + no open PR → `OPEN_PR`; different-sha + open PR already exists for that exact
      target → `NONE` (FR-004)
- [X] T010 [P] Unit test for `derive_branch_name` in
      `scripts/tests/test_check_submodule_updates.py`: two different target shas for
      the same submodule produce two different branch names; same (submodule, sha)
      pair is deterministic across calls

**Checkpoint**: Foundation ready — core detection/decision logic is implemented and unit
tested; user story work (wiring it to real submodules, the workflow, and PR creation) can
now begin

---

## Phase 3: User Story 1 - Detect a new upstream code build and propose it (Priority: P1) 🎯 MVP

**Goal**: A pull request bumping `data/w1-28-src`'s pointer appears automatically when the
real w1-28 upstream mirror has advanced.

**Independent Test**: Per spec.md — point the detection job at the real upstream mirror
with a known new commit past the currently pinned one, confirm a PR appears updating only
`data/w1-28-src`'s pointer, with no other files changed.

### Tests for User Story 1

- [X] T011 [P] [US1] Network-marked integration test in
      `scripts/tests/test_check_submodule_updates.py` (marked `network`, following
      `registry/tests/test_resolver.py`'s convention): `get_upstream_tip_sha` against
      the real `StefanMaron/MSDyn365BC.Sandbox.Code.History` `w1-28` branch returns a
      real 40-char sha, and `get_pinned_sha("data/w1-28-src")` against this repo's own
      checkout returns the real committed gitlink sha (compare against
      `git ls-tree HEAD -- data/w1-28-src` run directly in the test to avoid a tautology)

### Implementation for User Story 1

- [X] T012 [US1] Implement `bump_submodule_pointer(path, target_sha)` in
      `scripts/check_submodule_updates.py` via
      `git update-index --cacheinfo 160000,<target_sha>,<path>` + `git commit` on a new
      branch (research.md "Bump the pointer via git update-index --cacheinfo" decision;
      depends on T004-T008)
- [X] T013 [US1] Implement `pr_exists_for_branch(branch_name) -> bool` in
      `scripts/check_submodule_updates.py` via `gh pr list --head <branch_name>
      --state open --json number` (contracts/detection-job.md step 4)
- [X] T014 [US1] Implement `open_bump_pr(submodule, target_sha)` in
      `scripts/check_submodule_updates.py`: creates the branch, calls
      `bump_submodule_pointer`, pushes, and calls `gh pr create` with the title/body
      shape from data-model.md's "Bump pull request" table (`[auto] Bump <path> to
      <short-sha>` title, body naming old/new sha + linking the workflow run via
      `GITHUB_RUN_ID`/`GITHUB_SERVER_URL`/`GITHUB_REPOSITORY` env vars) — depends on
      T012, T013
- [X] T015 [US1] Wire a `main()` CLI entrypoint in
      `scripts/check_submodule_updates.py` (argparse, `--dry-run` flag per
      quickstart.md's "Local dry run" section) that iterates the watched submodules
      from T004, calls T005/T006/T007/T008 per submodule, and calls T014 only when
      `plan_action` returns `OPEN_PR` and `--dry-run` is not set — depends on T012-T014
- [X] T016 [US1] Create `.github/workflows/submodule-watch.yml` per
      contracts/detection-job.md's "Workflow trigger contract": `on.schedule` (daily
      cron) + `on.workflow_dispatch`, `permissions: contents: write,
      pull-requests: write` only, checks out the repo with `submodules: false` (per
      `ci.yml`'s existing posture), installs `uv`, runs
      `uv run python scripts/check_submodule_updates.py` — depends on T015

**Checkpoint**: User Story 1 fully functional — running the workflow against real drift
on `data/w1-28-src` produces a real PR

---

## Phase 4: User Story 2 - Detect new upstream docs and propose them (Priority: P2)

**Goal**: The same mechanism produces independent PRs for `data/docs` and
`data/docs-devitpro` when their upstream mirrors advance.

**Independent Test**: Per spec.md — pointed at the two docs mirrors instead, confirm each
produces its own PR updating only its own submodule pointer.

### Tests for User Story 2

- [X] T017 [P] [US2] Network-marked integration test in
      `scripts/tests/test_check_submodule_updates.py`: `get_upstream_tip_sha` against
      the real `MicrosoftDocs/dynamics365smb-docs` `main` branch and against
      `MicrosoftDocs/dynamics365smb-devitpro-pb` `main` branch both return real 40-char
      shas, and `get_pinned_sha` for `data/docs`/`data/docs-devitpro` matches
      `git ls-tree HEAD` for each

### Implementation for User Story 2

- [X] T018 [US2] Add `data/docs` and `data/docs-devitpro` entries to the
      `WatchedSubmodule` config from T004 (if not already included there — T004 should
      define all three from the start per data-model.md, making this task a
      verification/no-op check rather than new code; confirm via T017 passing for both)
- [X] T019 [US2] Verify in `scripts/tests/test_check_submodule_updates.py` (unit test,
      not network) that `main()`/the per-submodule loop from T015 processes all three
      watched submodules independently — one submodule's `plan_action` result
      (`NONE`/`OPEN_PR`) has no effect on another's, and each `OPEN_PR` call produces
      its own separate branch name (FR-003: never bundle two submodules into one PR)

**Checkpoint**: All three watched submodules (w1-28-src, docs, docs-devitpro) are
independently detected and proposed

---

## Phase 5: User Story 3 - Every proposed update still goes through the existing safety gate (Priority: P1)

**Goal**: Automated PRs are indistinguishable from hand-made ones in terms of CI/merge
safety — nothing in this feature can merge a PR.

**Independent Test**: Per spec.md — confirm an opened PR runs the exact same CI as a
hand-made bump PR, is never merged by anything other than an explicit human action, and
no code path in this feature calls a merge operation.

### Tests for User Story 3

- [X] T020 [P] [US3] Static check test in
      `scripts/tests/test_check_submodule_updates.py`: assert the literal string
      `"pr merge"` / `gh pr merge` does not appear anywhere in
      `scripts/check_submodule_updates.py`'s source (a cheap, durable regression guard
      against a future edit accidentally adding a merge call — read the file's own
      source text in the test rather than mocking `subprocess`)
- [X] T021 [P] [US3] Static check test in
      `scripts/tests/test_check_submodule_updates.py` (or a workflow-linting test if
      the repo has a YAML test convention — check first): assert
      `.github/workflows/submodule-watch.yml`'s `permissions:` block contains no
      merge-capable scope (i.e. contains only `contents: write` and
      `pull-requests: write`, not e.g. `contents: admin` or an org-level token)

### Implementation for User Story 3

- [X] T022 [US3] Confirm (no code change expected — this is a verification task) that
      `.github/workflows/submodule-watch.yml` from T016 already satisfies FR-005/FR-006:
      scoped permissions only, no merge step, and that the checked-out `master` branch's
      existing branch-protection / required-CI-check settings on GitHub (not part of
      this repo's tracked files) still apply to PRs opened by `github-actions[bot]` the
      same as any other PR — verify directly on GitHub's repo settings, don't assume.
      **Verified live** via `gh api repos/StefanMaron/bc-code-atlas/branches/master/protection`:
      `enforce_admins: true`, `allow_force_pushes: false`, required status checks apply
      to every PR regardless of author. **Follow-up noted, not applied**: the new
      `test (scripts)` CI job isn't yet in the required-status-checks list (it's a fixed
      list on the branch protection rule) — this doesn't weaken FR-005/FR-006 (a human
      still must merge either way), but the maintainer may want to add it as required
      after this PR's first green run, via repo Settings → Branches (a branch-protection
      change, deliberately not made automatically here).
- [X] T023 [US3] Add a one-line note to `CLAUDE.md`'s existing "Known open items"
      section (or wherever the reindex-webhook gap is currently documented) marking it
      resolved and pointing at `scripts/check_submodule_updates.py` +
      `.github/workflows/submodule-watch.yml`, per this project's established pattern
      of keeping CLAUDE.md's operational history current (see the existing "bumping
      tools/graphify-al" checklist section added earlier this session for the expected
      style)

**Checkpoint**: All three user stories independently functional; feature is complete per
spec.md

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T024 [P] Run `scripts/tests` with `pytest -m "not network"` and confirm it's fast
      and green without network access, matching `registry/tests`' existing skip
      convention
- [ ] T025 Run quickstart.md's full validation sequence end-to-end (dry run, manual
      workflow trigger, real PR inspection, duplicate-PR re-run check, closed-PR
      re-propose check) against the real repository once T001-T023 are merged
- [X] T026 [P] Update `README.md` if it documents the deploy/maintenance pipeline
      (check first) to mention that submodule bumps for the default corpus can now
      arrive automatically via `submodule-watch.yml`, not only by hand

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational only
- **User Story 2 (Phase 4)**: Depends on Foundational; reuses US1's `main()`/workflow
  (T015/T016) rather than duplicating them — in practice implement after US1, though its
  own tasks (T017-T019) don't strictly require US1's PR-creation code (T012-T014) to be
  finished first, only the config/loop from T004/T015
- **User Story 3 (Phase 5)**: Depends on Foundational + US1's workflow file (T016)
  existing to verify against — cannot be meaningfully tested before T016 lands, even
  though its own guard tests (T020-T021) are about the script/workflow's *shape*, not
  behavior
- **Polish (Phase 6)**: Depends on all three user stories being complete

### Parallel Opportunities

- T005/T006 (independent git-plumbing functions) in parallel
- T009/T010 (independent unit test files/functions) in parallel
- T011 and T017 (US1 and US2's network tests) in parallel once Foundational is done —
  they hit different upstream repos and touch no shared state
- T020/T021 (US3's two static checks) in parallel

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: User Story 1 — this alone delivers real value (automated `w1-28-src`
   bump PRs, the highest-priority/highest-frequency drift per spec.md's own priority
   rationale)
4. **STOP and VALIDATE**: run T011's real integration test, then a real
   `workflow_dispatch` trigger against a real (or temporarily rolled-back) pinned sha
5. Add User Story 2 (docs) — mechanically small once US1's loop exists (T018-T019 are
   mostly verification, not new logic)
6. Add User Story 3's explicit verification tasks (T020-T023) — the safety properties
   they check are true by construction from T014/T016's design, but per spec.md's own
   framing this is "equally critical" to US1, so verify it explicitly rather than assume
   the construction was correct

### Incremental Delivery

Setup + Foundational → US1 (MVP, real code-drift PRs) → US2 (docs PRs) → US3
(safety-gate verification) → Polish. Each checkpoint is independently demoable: after
US1, a maintainer already gets real value (no more manually noticing w1-28 drift); US2
and US3 round it out without changing US1's already-shipped behavior.

---

## Notes

- [P] tasks = different files or independent functions, no ordering dependency
- [Story] label maps task to specific user story for traceability
- This feature has no models/entities requiring a database (data-model.md) — "model"
  tasks from the generic template are replaced with the config/decision-function tasks
  above
- Commit after each task or logical group, per this repo's existing PR-per-submodule-bump
  convention — this feature's own implementation should itself land as one focused PR
  through the standard `/speckit-implement` → CI → merge flow, not as three separate PRs
  per user story (the three stories share one script/workflow file, unlike the runtime
  PRs this feature produces)
