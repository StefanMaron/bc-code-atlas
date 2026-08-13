# Data Model: Automated Default-Corpus Update Detection

This feature has no database and no new persistent storage (spec.md Assumptions: GitHub's
own open-PR list is the only "already proposed" state, see research.md's duplicate-PR
guard decision). The two entities from spec.md's Key Entities section are represented as
plain in-memory values inside the detection script, not stored rows.

## Watched submodule

One of the three data submodules this feature monitors. Not a database row -- a small
static config list in the script (or read directly from `.gitmodules`, since that already
carries `path`, `url`, and `branch` for each).

| Field | Type | Source | Notes |
|---|---|---|---|
| `path` | string | `.gitmodules` | e.g. `data/w1-28-src` |
| `upstream_url` | string | `.gitmodules` | e.g. `https://github.com/StefanMaron/MSDyn365BC.Sandbox.Code.History.git` |
| `branch` | string | `.gitmodules` | e.g. `w1-28`, `main` |
| `pinned_sha` | string | `git ls-tree HEAD -- <path>` | Currently committed gitlink on the default branch |
| `upstream_tip_sha` | string | `git ls-remote <upstream_url> <branch>` | Real-time upstream branch head |

`tools/graphify-al` and other tooling submodules are present in `.gitmodules` but are
explicitly excluded from the watched set (spec.md Assumptions) -- the script's config
list names only the three in scope; it does not derive the watched set from
`.gitmodules` wholesale.

## Bump pull request

A pull request that changes exactly one watched submodule's pinned commit. Not stored
anywhere by this feature -- it *is* a GitHub pull request, and its own existence on
GitHub is the record.

| Field | Type | Source | Notes |
|---|---|---|---|
| `head_branch` | string | derived: `bot/bump-<submodule-slug>-<short-sha>` | Deterministic per (submodule, target commit) -- doubles as the duplicate-PR lookup key (research.md) |
| `submodule_path` | string | Watched submodule's `path` | Which of the three this PR touches |
| `target_sha` | string | Watched submodule's `upstream_tip_sha` at detection time | What the gitlink is bumped to |
| `title` | string | `[auto] Bump <path> to <short-sha>` | `[auto]` prefix satisfies FR-009 |
| `body` | string | generated | Names the submodule, old sha, new sha, and links the workflow run that opened it |
| `author` | identity | `github-actions[bot]` (via `GITHUB_TOKEN`) | Bot authorship satisfies FR-009/SC-004 without opening the PR |

No state transitions are modeled here -- a bump pull request's lifecycle (open → CI
green → merged/closed) is entirely GitHub's own, not something this feature tracks or
drives beyond the initial open (User Story 3 / FR-005: this feature never merges).
