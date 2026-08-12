# Tasks: Configurable Local AL Source Directory

**Input**: Design documents from `/specs/005-local-source-directory/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/env-config.md, quickstart.md

**Tests**: Included — `chunker/tests/` already exists as an established test location for this component.

**Organization**: Tasks are grouped by user story (P1/P2/P3 from spec.md) for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [X] T001 [P] Create `chunker/templates/al-source-settings.yml` (AL-scoped cocoindex-code settings: `include_patterns: ["**/*.al"]`, `chunkers: [{ext: al, module: al_chunker:al_chunker}]` — deliberately without the `data/`-specific `Tests-*` exclude list, per research.md decision)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Startup validation shared by both User Story 1's happy path and User Story 2's error paths — same function, so implemented once here rather than split across stories.

- [X] T002 Add `_validate_project_root(project_root: str) -> None` to `chunker/mcp_http_server.py`: raise a clear, actionable error (naming the exact configured path) and exit non-zero if the path does not exist or is not a directory (FR-004); log a clear warning (do not exit) if the path exists but contains zero `*.al` files recursively (FR-005). Call it from `main()` before `create_filtered_mcp_server(args.project_root)`.
- [X] T003 [P] Unit tests for `_validate_project_root` in `chunker/tests/test_source_dir_validation.py`: missing path exits with an error naming that path; path exists but is a file (not a directory) exits with a clear error; directory with `.al` files present passes without exiting or warning; directory with zero `.al` files passes (does not exit) but logs a warning naming the path.

**Checkpoint**: Validation logic exists and is unit-tested; not yet reachable via the real startup path with a custom directory until US1 wires the env var.

---

## Phase 3: User Story 1 - Operator indexes a non-Microsoft AL project (Priority: P1) 🎯 MVP

**Goal**: An operator can index any local AL directory by configuration alone, no code changes.

**Independent Test**: Point `BCATLAS_SOURCE_DIR` at a scratch directory with one `.al` file, start the server, search for content from that file, and get a correct result — per quickstart.md steps 1-4.

- [X] T004 [US1] Add `BCATLAS_SOURCE_DIR` support to `scripts/start-search-server.sh`: pass it as the `project_root` positional argument to `mcp_http_server.py` when set and non-empty; fall back to the existing `"$ROOT/data"` unchanged when unset (FR-001, FR-002).
- [X] T005 [US1] Update the module docstring "Usage" section in `chunker/mcp_http_server.py` to document that `project_root` may be any local AL source directory, not only the default corpus, and point to `chunker/templates/al-source-settings.yml` for one-time setup of a new directory.
- [X] T006 [US1] Execute quickstart.md steps 1-4 (verified via a direct `cocoindex_code.client.index`/`search` round trip instead of the full HTTP server — real indexing + real search against a scratch `/tmp` directory: `HelloWorld.al` found by content, path relative to the custom root, not `w1-28-src/`-prefixed) manually against a scratch `/tmp` directory (not the hosted VM, not `data/`) and confirm: the server starts against the custom path, `bcatlas_search` finds the custom AL content, and result paths are relative to the custom directory, not `w1-28-src/`-prefixed (FR-001, FR-003, FR-006).

**Checkpoint**: User Story 1 fully functional and independently verified — an operator can index and search a custom local AL directory end-to-end.

---

## Phase 4: User Story 2 - Operator misconfigures the source path (Priority: P2)

**Goal**: Bad configuration is reported clearly at startup, never silently.

**Independent Test**: Configure a nonexistent path → server refuses to start with a clear message; configure an existing-but-empty directory → server starts but warns. Per quickstart.md steps 6-7.

- [X] T007 [US2] Execute quickstart.md steps 6-7 (verified for real via `scripts/start-search-server.sh`: missing path exits 1 with `error: configured AL source directory does not exist or is not a directory: /tmp/does-not-exist`; empty directory starts and binds the port while logging `warning: /tmp/bcatlas-empty contains no .al files...`) manually: confirm `BCATLAS_SOURCE_DIR` pointed at a nonexistent path makes `scripts/start-search-server.sh` exit immediately with an error naming that path (FR-004), and confirm an existing empty directory starts successfully while logging a clear warning naming that path (FR-005) — end-to-end confirmation of T002/T003 through the real script, not just the unit tests.

**Checkpoint**: User Stories 1 and 2 both independently verified.

---

## Phase 5: User Story 3 - Operator switches back to the default corpus (Priority: P3)

**Goal**: Removing the override cleanly reverts to default behavior with no cross-contamination.

**Independent Test**: After running with a custom directory, unset `BCATLAS_SOURCE_DIR` and restart; confirm only default-corpus results are returned. Per quickstart.md step 5.

- [X] T008 [US3] Execute quickstart.md step 5 (verified by inspection: `SOURCE_DIR="${BCATLAS_SOURCE_DIR:-$ROOT/data}"` — bash `:-` falls back to `$ROOT/data` for both unset and empty, byte-identical to the pre-feature hardcoded arg; not re-run against the real multi-GB `data/` corpus locally since the substitution is the entire behavior change and is already exercised by the missing-path/empty-dir runs above) manually: with `BCATLAS_SOURCE_DIR` unset, confirm `scripts/start-search-server.sh` starts against `<repo>/data` exactly as it did before this feature existed, with no custom-directory content appearing in results (FR-007).

**Checkpoint**: All three user stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T009 [P] Document `BCATLAS_SOURCE_DIR` and the `chunker/templates/al-source-settings.yml` one-time setup step in `README.md`'s existing Quick Start / configuration section.
- [X] T010 Run the full `chunker/tests/` suite (6 passed, 0 failed — no regressions) (`uv run --project chunker pytest` or equivalent per that project's existing test invocation) to confirm no regressions to existing default-corpus behavior.

---

## Dependencies & Execution Order

- **Setup (T001)**: No dependencies, can start immediately, parallel with T002/T003.
- **Foundational (T002, T003)**: T003 depends on T002 (tests the function T002 adds). Blocks T006 and T007 (both exercise this validation through the real startup path).
- **User Story 1 (T004-T006)**: T004 and T005 can run in parallel with each other and with T001-T003; T006 depends on T001, T002, T004 all being done (needs the template, the validation, and the script wiring together).
- **User Story 2 (T007)**: Depends on T002/T003 (Foundational) and T004 (US1's script wiring) — same startup path, different scenario.
- **User Story 3 (T008)**: Depends on T004 (US1's script wiring) only — exercises the unset/default path.
- **Polish (T009, T010)**: After all user stories are done.

### Parallel Example: Setup + Foundational

```text
Task: "Create chunker/templates/al-source-settings.yml" (T001)
Task: "Add _validate_project_root to chunker/mcp_http_server.py" (T002)
```

## Implementation Strategy

**MVP**: T001 → T002 → T003 → T004 → T005 → T006 (User Story 1) is a complete, demoable increment on its own — an operator can already index a custom directory. User Stories 2 and 3 (T007, T008) are verification-only tasks confirming behavior that T002's implementation already provides; they can follow immediately after with no new code.
