# Implementation Plan: Automated Default-Corpus Update Detection

**Branch**: `008-reindex-webhook` | **Date**: 2026-08-13 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/008-reindex-webhook/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

A scheduled GitHub Actions workflow checks the three watched upstream mirrors
(`data/w1-28-src`, `data/docs`, `data/docs-devitpro`) for commits past what's currently
pinned, and opens one pull request per advanced submodule that bumps only that
submodule's gitlink -- reusing `git ls-remote`/`git ls-tree`/`git update-index
--cacheinfo` to detect and propose the bump without checking out any of the large data
submodules, and `gh pr create` (under a `contents: write, pull-requests: write`-only
token, never merge-capable) to open it. No new service, storage, or deploy path --
opened PRs ride the existing `ci.yml` → merge → `deploy.yml` → `deploy-vm.sh` pipeline
unchanged (see research.md for each decision's rationale).

## Technical Context

**Language/Version**: Python 3.13 (matches `registry`/`build`'s existing convention),
invoked from a GitHub Actions workflow step -- no new runtime introduced.

**Primary Dependencies**: Standard library only (`subprocess` for `git`/`gh` CLI calls,
same posture as `registry/registry/git_ops.py`). No new third-party package.

**Storage**: N/A -- stateless; GitHub's own open-PR list is the "already proposed" record
(research.md's duplicate-PR guard decision), no new database or file-based state.

**Testing**: pytest, split the same way `registry/tests` already is: fast unit tests for
the pure decision logic (given pinned/upstream shas and existing-PR state, what action
follows) plus a `network`-marked integration test hitting the three real upstream mirrors
via `git ls-remote` (constitution Principle V -- measure against real upstream, not a
mock), skippable via the existing `pytest -m "not network"` convention.

**Target Platform**: GitHub Actions `ubuntu-latest` runner, scheduled (`on.schedule`) plus
`on.workflow_dispatch` for manual runs.

**Project Type**: CLI script + GitHub Actions workflow (not a served application --
distinct from `registry`/`build`'s always-on MCP services).

**Performance Goals**: N/A in the req/s sense -- a daily job; each submodule check is a
single `ls-remote` call, sub-second per submodule.

**Constraints**: Must never check out `data/w1-28-src`/`data/docs`/`data/docs-devitpro`
content (multi-GB, matches `ci.yml`'s existing `submodules: false` posture); must never
merge a PR it opens (FR-005); must isolate one watched mirror's failure from the other two
(FR-008).

**Scale/Scope**: 3 watched submodules (fixed set per spec.md Assumptions --
`tools/graphify-al` and other tooling submodules explicitly excluded).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Serve Like It's Remote** -- N/A. This feature is a CI/repo-maintenance job, not a
  capability reachable by a BC/AL developer's coding agent; there is no MCP tool surface
  to serve remotely.
- **II. Build and Serve Are Separate Resource Pools** -- Respected by construction: this
  job never calls the build queue or the serving daemon directly. It only opens a PR;
  any eventual index rebuild happens exactly as it already does today when a human merges
  a hand-made bump PR, through the existing, unchanged deploy path.
- **III. Historical Versions Are Immutable — Only Tips Move** -- Directly matches this
  feature's shape: it exists specifically to catch a country/docs branch's *tip* moving,
  never proposes touching a historical build.
- **IV. Unbounded Scope, Bounded Residency** -- N/A. This feature doesn't touch warm
  (country, version) artifact residency at all; it only concerns the three data
  submodules backing the always-warm default corpus.
- **V. Measure, Don't Assume** -- Followed: every detection call (`git ls-remote`,
  `git ls-tree`) is a real, direct check against upstream/local git state, never a cached
  or inferred value (research.md).
- **VI. Minimal, Justified Forks** -- No new fork or vendored dependency; the job composes
  stock `git`/`gh` CLI calls already available on GitHub-hosted runners.
- **VII. Lean, Honest Agent-Facing Output** -- N/A. No MCP tool output is produced by this
  feature.
- **VIII. Deploys Must Not Reset the Serving Index** -- Respected: this feature changes
  nothing about how a merge reaches the VM (`deploy.yml`/`deploy-vm.sh` unchanged). A
  merged submodule bump was already going to trigger the same restart-and-incrementally-
  reindex path this principle protects, for any bump PR, hand-made or automated --
  this feature doesn't add new risk here, it only automates *proposing* the PR.

No violations requiring Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
scripts/
├── check_submodule_updates.py   # detection + PR-open logic (CLI entrypoint)
└── tests/
    └── test_check_submodule_updates.py   # unit tests (fast) + network-marked
                                           # integration test against the real
                                           # three upstream mirrors

.github/workflows/
└── submodule-watch.yml          # schedule + workflow_dispatch trigger,
                                  # contents:write / pull-requests:write only
```

**Structure Decision**: New top-level `scripts/` module (this repo has no existing
top-level scripts package with tests -- `scripts/deploy-vm.sh` and
`scripts/wait-for-search-ready.py` exist today but are un-tested shell/one-off glue, not
a precedent for this feature's testable Python logic). Kept independent of
`registry/`/`build/` rather than added to either: this feature doesn't serve MCP tools
and doesn't belong to either of their `pyproject.toml`s, and per constitution "Prefer
composing existing, verified primitives" it reuses the *pattern* established by
`registry/registry/git_ops.py` (thin subprocess wrappers around real `git` calls,
network-marked tests) without importing `registry` as a runtime dependency for a
three-call job (research.md).

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
