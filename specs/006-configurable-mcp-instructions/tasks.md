# Tasks: Configurable MCP Instructions and Path Filtering

**Input**: Design documents from `/specs/006-configurable-mcp-instructions/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/settings-file.md, quickstart.md

**Tests**: Included — matches `chunker/tests/` convention established for issue #18.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Foundational (Blocking Prerequisites)

**Purpose**: The settings-loading function is shared by both User Story 1 (instructions) and User Story 2 (path prefixes) — same function, same validation path (FR-005), implemented once.

- [X] T001 Add `_load_presentation_settings(project_root: str) -> tuple[str, tuple[str, ...]]` to `chunker/mcp_http_server.py`: reads `<project_root>/.bcatlas/mcp_presentation.yml` if present; returns `(instructions_text, path_prefixes)`. Missing file → `(_MCP_INSTRUCTIONS, ())` sentinel meaning "use dynamic detection" (see T002). Present-and-valid → parsed `instructions` (str, default `_MCP_INSTRUCTIONS`) and `path_prefixes` (tuple of str, default `None` sentinel meaning "use dynamic detection"). Present-and-invalid (bad YAML, non-mapping, wrong field types) → `SystemExit` naming the file path and the problem (FR-005).
- [X] T002 [P] Unit tests for `_load_presentation_settings` in `chunker/tests/test_presentation_settings.py`: no file → defaults; file with only `instructions` → custom instructions + default-detection path prefixes; file with only `path_prefixes` → default instructions + custom prefixes; file with both → both custom; malformed YAML → `SystemExit` naming the file; non-mapping top level → `SystemExit`; `instructions` wrong type → `SystemExit`; `path_prefixes` wrong type (not a list of str) → `SystemExit`; empty `path_prefixes: []` → treated as "no prefixes" (not the same as "use dynamic detection" — Edge Cases in spec.md).

**Checkpoint**: Loader exists and is unit-tested; not yet wired into server construction.

---

## Phase 2: User Story 1 - Operator sees accurate tool instructions (Priority: P1) 🎯 MVP

**Goal**: A connecting MCP client receives configured instructions text instead of the hardcoded BC description, when configured.

**Independent Test**: Configure custom `instructions`, start the server, connect a client, confirm reported instructions match. Per quickstart.md steps 1-4 (instructions half).

- [X] T003 [US1] Wire `_load_presentation_settings` into `create_filtered_mcp_server(project_root)` in `chunker/mcp_http_server.py`: replace the hardcoded `_MCP_INSTRUCTIONS` passed to `FastMCP("cocoindex-code", instructions=_MCP_INSTRUCTIONS)` with the loaded value (FR-001, FR-002).
- [X] T004 [US1] Execute quickstart.md steps 1-4 manually against a scratch directory: confirm a connected MCP client sees the configured custom instructions text, and confirm a separate default-corpus run (no `.bcatlas/` dir) still reports the exact existing BC instructions text unchanged.

**Checkpoint**: User Story 1 fully functional and independently verified.

---

## Phase 3: User Story 2 - Operator configures path-filter prefixes (Priority: P2)

**Goal**: Prefix-agnostic path filters expand against operator-configured prefixes instead of (or in addition to falling back to) the hardcoded default candidate list.

**Independent Test**: Configure custom `path_prefixes`, issue a `bcatlas_search` with a prefix-agnostic `paths` filter, confirm expansion uses the configured prefixes.

- [X] T005 [US2] In `create_filtered_mcp_server`, replace the call to `_resolve_corpus_path_prefixes(project_root)` with: use the loaded `path_prefixes` from T001 when the settings file configured them explicitly (including the empty-list case, FR-004 Edge Case); otherwise fall back to the existing `_resolve_corpus_path_prefixes(project_root)` dynamic-detection call unchanged (FR-004).
- [X] T006 [US2] Execute quickstart.md's path-prefix scenario manually (extend step 1's settings file to include a `paths`-filtered `bcatlas_search` call against a subdirectory matching the configured prefix): confirm a prefix-agnostic filter is correctly expanded using the configured prefix.

**Checkpoint**: User Stories 1 and 2 both independently verified.

---

## Phase 4: User Story 3 - Settings-file-only updates (Priority: P3)

**Goal**: Confirm changing configuration requires no code change — implied by T001/T003/T005 already reading from a file, verified explicitly here.

- [X] T007 [US3] Execute quickstart.md end-to-end once more after only editing the `.bcatlas/mcp_presentation.yml` file from step 1 (change the instructions text) and restarting the server (no code edits between runs): confirm the newly edited text is served.

**Checkpoint**: All three user stories independently functional.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [X] T008 [P] Document `.bcatlas/mcp_presentation.yml` in `chunker/mcp_http_server.py`'s module docstring (alongside the `BCATLAS_SOURCE_DIR` note added for issue #18) and in `README.md`'s "Indexing a different local AL source directory" section added for issue #18.
- [X] T009 (16/16 passed, no regressions) Run the full `chunker/tests/` suite to confirm no regressions to issue #18's tests or existing default-corpus behavior.

---

## Dependencies & Execution Order

- **Foundational (T001, T002)**: T002 depends on T001. Blocks T003 and T005 (both wire the loader into `create_filtered_mcp_server`).
- **User Story 1 (T003, T004)**: T003 depends on T001. T004 depends on T003.
- **User Story 2 (T005, T006)**: T005 depends on T001 (independent of T003, but both edit the same function body — implement sequentially to avoid merge conflicts, not because of a logical dependency). T006 depends on T005.
- **User Story 3 (T007)**: Depends on T003 and T005 both being done (exercises both settings fields together).
- **Polish (T008, T009)**: After all user stories.

## Implementation Strategy

**MVP**: T001 → T002 → T003 → T004 (User Story 1) is a complete, demoable increment — accurate instructions for a custom corpus. User Story 2 (path prefixes) and User Story 3 (pure-config-change verification) layer on top with no new architecture.
