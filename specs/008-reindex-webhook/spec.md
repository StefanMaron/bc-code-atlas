# Feature Specification: Automated Default-Corpus Update Detection

**Feature Branch**: `008-reindex-webhook`

**Created**: 2026-08-13

**Status**: Draft

**Input**: User description: "Automated reindex-webhook wiring for the always-warm default corpus. Currently, keeping data/w1-28-src, data/docs, and data/docs-devitpro (the code+docs submodules backing the default served corpus) in sync with their real upstream mirrors requires someone to manually notice new upstream commits, bump the submodule pointer(s), open a PR, wait for CI, and merge -- the exact manual flow just used for the tools/graphify-al bump this session. There is no automatic detection of new upstream commits and no automatic PR creation. Build a scheduled (or webhook-triggered, whichever research finds more reliable/available) job that: detects when the real upstream mirrors for w1-28 source, business-central docs, and devitpro docs have new commits past what's currently pinned; bumps the relevant submodule pointer(s) in this repo; and opens a PR the same way the graphify-al bump PRs in this session were opened (so it goes through the existing CI -> merge -> deploy.yml -> deploy-vm.sh pipeline unchanged, no new deploy mechanism needed). Should not auto-merge -- opening the PR and letting the existing CI gate + a human merge decision stand is the intended scope, consistent with how every other submodule bump in this repo has been handled."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Detect a new upstream code build and propose it (Priority: P1)

As the project maintainer, when Microsoft's real w1-28 base-application source mirror gets a new build past what `data/w1-28-src` currently points to, I want a pull request to already be waiting for me that bumps the pointer to the new commit -- instead of me having to remember to periodically check for new builds by hand.

**Why this priority**: This is the core, most valuable case -- code drift is what the project's own default corpus exists to serve accurately, and it changes more often than docs.

**Independent Test**: Can be fully tested by pointing the detection job at a real upstream mirror with a known new commit past the currently pinned one, and confirming a pull request appears that updates only `data/w1-28-src`'s pointer to that commit, with no other files changed.

**Acceptance Scenarios**:

1. **Given** the real upstream w1-28 mirror has a new commit beyond `data/w1-28-src`'s currently pinned commit, **When** the detection job next runs, **Then** a new pull request is opened against this repository that updates only the `data/w1-28-src` submodule pointer to the new commit.
2. **Given** `data/w1-28-src` is already pinned to the upstream mirror's latest commit, **When** the detection job runs, **Then** no pull request is opened.
3. **Given** a pull request from a previous run proposing a bump to a specific commit is still open and unmerged, **When** the detection job runs again and the upstream mirror has not advanced further, **Then** no duplicate pull request is opened for the same commit.

---

### User Story 2 - Detect new upstream docs and propose them (Priority: P2)

As the project maintainer, when the public Business Central docs or AL developer/compiler reference docs get new commits past what `data/docs`/`data/docs-devitpro` currently point to, I want the same kind of pull request opened automatically, so the served docs corpus doesn't quietly fall behind while attention stays on code changes.

**Why this priority**: Docs drift matters but changes less often and is lower-stakes than code drift (stale docs are misleading; stale code is a correctness gap) -- real value, but secondary to User Story 1.

**Independent Test**: Can be fully tested the same way as User Story 1, pointed at the two docs mirrors instead, confirming each produces its own pull request updating only its own submodule pointer.

**Acceptance Scenarios**:

1. **Given** the real upstream business-central docs mirror has a new commit beyond `data/docs`'s currently pinned commit, **When** the detection job next runs, **Then** a pull request is opened updating only `data/docs`'s pointer.
2. **Given** the real upstream devitpro docs mirror has a new commit beyond `data/docs-devitpro`'s currently pinned commit, **When** the detection job next runs, **Then** a separate pull request is opened updating only `data/docs-devitpro`'s pointer.

---

### User Story 3 - Every proposed update still goes through the existing safety gate (Priority: P1)

As the project maintainer, I want every automatically proposed update to go through the exact same CI checks and human merge decision that a manual bump PR goes through today -- never merged automatically -- so a bad upstream commit, an upstream history rewrite, or an unexpected regression can't reach the live serving VM without a person approving it.

**Why this priority**: This is a safety constraint on the whole feature, equally critical to User Story 1 -- automating detection without this would turn a manual, reviewed process into an unreviewed one.

**Independent Test**: Can be fully tested by confirming an opened pull request from this job runs the exact same CI workflow as a hand-made bump PR, is not merged by anything other than an explicit human action, and that no code path in this feature calls a merge operation.

**Acceptance Scenarios**:

1. **Given** a pull request opened by the detection job, **When** it is viewed on GitHub, **Then** it triggers the same CI checks that run on any other pull request against this repository.
2. **Given** a pull request opened by the detection job passes all CI checks, **When** no human merges it, **Then** it remains open and unmerged indefinitely -- nothing in the system merges it automatically.

---

### Edge Cases

- What happens when the upstream mirror's branch history has been rewritten (force-pushed) since the currently pinned commit, so the pinned commit is no longer an ancestor of the new tip? The job must still be able to propose the new tip as the bump target (a fast-forward check must not be a hard requirement), consistent with real drift already observed and handled manually for the w1-28 mirror (see registry's own resolver tests).
- What happens when the detection job cannot reach a given upstream mirror at all (network failure, mirror temporarily unavailable)? That mirror's check must fail safely without opening an incorrect or empty pull request, and must not block detection/proposal for the other, unaffected mirrors.
- What happens when a previously opened bump pull request was closed without merging (rejected)? The next run must be able to propose a bump again (to the same or a newer commit) rather than treating that submodule as permanently skipped.
- What happens when two or more of the three watched submodules (`data/w1-28-src`, `data/docs`, `data/docs-devitpro`) have advanced at the same time? Each gets its own independent pull request (per User Story 2's acceptance scenario 2), not one bundled pull request touching multiple submodules.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST periodically check whether the real upstream mirror for each of the three watched submodules (`data/w1-28-src`'s w1-28 branch, `data/docs`, `data/docs-devitpro`) has a commit beyond what that submodule currently points to in this repository's default branch.
- **FR-002**: System MUST open a pull request against this repository's default branch when a watched submodule's upstream mirror has advanced, changing only that submodule's pinned commit (no other files).
- **FR-003**: System MUST NOT bundle updates to more than one submodule into a single pull request -- each advanced submodule gets its own independent pull request.
- **FR-004**: System MUST NOT open a duplicate pull request proposing the same target commit for a submodule when an open, unmerged pull request already proposes that exact bump.
- **FR-005**: System MUST NOT merge any pull request it opens, and MUST NOT include any mechanism (automatic or conditional) that could merge one without an explicit human action.
- **FR-006**: Every pull request opened by this system MUST trigger this repository's existing CI checks unchanged -- the system MUST NOT introduce a separate or reduced validation path for its own pull requests.
- **FR-007**: System MUST correctly detect and propose an upstream commit as a bump target even when that commit is not a descendant of the currently pinned commit (i.e., the upstream mirror's history was rewritten), matching real, previously observed behavior of these upstream mirrors.
- **FR-008**: When a given watched mirror is unreachable during a check, system MUST skip only that mirror's check for that run (no pull request opened or altered for it) and MUST still complete checks for the other watched mirrors.
- **FR-009**: System MUST identify each pull request it opens (e.g. in its title, description, or authoring identity) as automatically generated, so a reviewer can distinguish it from a hand-authored bump PR at a glance.

### Key Entities

- **Watched submodule**: One of the three data submodules this feature monitors (`data/w1-28-src`, `data/docs`, `data/docs-devitpro`). Each has its own upstream mirror location and its own currently-pinned commit, tracked independently of the others.
- **Bump pull request**: A pull request that changes exactly one watched submodule's pinned commit to a specific newer upstream commit. Carries enough information (target commit, which submodule) for both the automated duplicate check (FR-004) and a human reviewer to evaluate it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: When a real new commit lands on any of the three watched upstream mirrors, a corresponding pull request appears in this repository within one detection cycle, without any person having to notice the upstream change themselves.
- **SC-002**: Zero pull requests opened by this system are ever merged without an explicit human merge action, across all runs.
- **SC-003**: No duplicate pull requests accumulate for the same submodule while an equivalent one is already open and unmerged.
- **SC-004**: A maintainer reviewing the repository's open pull requests can identify, without opening any of them, which ones were opened by this automated system versus opened by a person.

## Assumptions

- Scope is limited to the three data submodules named in the feature description (`data/w1-28-src`, `data/docs`, `data/docs-devitpro`). `tools/graphify-al` and the other tooling submodules are explicitly out of scope -- those are separate vendored forks with their own manual review/port workflow (see this session's `tools/graphify-al` bump), not a passive upstream-mirror pull.
- "New commit" means the upstream mirror's relevant branch tip has moved past the currently pinned commit; this feature does not need to detect or propose new countries/branches that aren't already tracked by an existing watched submodule.
- The existing `deploy.yml` → `deploy-vm.sh` pipeline, unchanged, is what eventually applies a merged bump to the live serving VM -- this feature's scope ends at opening a mergeable pull request; it does not need its own deploy step.
- A reasonable default detection cadence (e.g. daily) is acceptable unless real drift frequency observed during implementation suggests otherwise; the exact cadence is not user-facing and does not need to be pinned down in this specification.
- One pull request per advanced submodule (never bundled) is the intended shape, consistent with how every prior submodule bump in this repository (both `tools/graphify-al` and any other tooling submodule) has been done as its own standalone PR.
