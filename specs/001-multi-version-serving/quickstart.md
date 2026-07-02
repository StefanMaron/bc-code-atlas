# Quickstart: Validating Multi-Country, Multi-Version Serving

Validates the three user stories end-to-end from a separate MCP client session, per
constitution Principle I — not by reading source or calling internals directly.

## Prerequisites

- All servers running per the updated `README.md` Quick Start: search (`:8801`), graph
  (`:8802`), the new registry server (`:8803`), the new build server (`:8804`), aggregator
  (`:8800`).
- A separate Claude Code session (or any MCP client) configured with `.mcp.json` pointing at
  `http://localhost:8800/mcp`, matching the existing `client-session/` pattern.

## US1 — Discover and resolve a version

```
call bcatlas_list_countries
  -> expect a short, finite list including "w1", "us", "de" (or whichever real countries
     the upstream repo currently has)

call bcatlas_list_versions { "country": "w1" }
  -> expect a summarized list of major.minor versions, not thousands of raw builds

call bcatlas_resolve_version { "country": "w1", "spec": "28.1" }
  -> expect resolved: true, exactly one commit_sha/version_string, the highest build number
     within 28.1

call bcatlas_resolve_version { "country": "w1", "spec": "not-a-real-version" }
  -> expect resolved: false, reason: "not_found" -- never a guessed fallback
```

## US2 — Diff across versions

Pick a real symbol known to have changed between two resolved versions (e.g. from the
269-file delta already measured between `w1-28.1.49838.50848` and the `w1-28.2` tip this
session — confirm current file list since the tip has since advanced).

```
call bcatlas_diff {
  "country": "w1", "from_spec": "28.1", "to_spec": "28.2",
  "scope": "symbol", "object_type": "codeunit", "object_name": "<real object>",
  "procedure_name": "<real procedure known to have changed>"
}
  -> expect diff_text showing only that procedure's change, from_found/to_found both true

call bcatlas_diff {
  "country": "w1", "from_spec": "28.1", "to_spec": "28.2"
}  # no scope
  -> expect an explicit rejection, never a whole-repo diff

call bcatlas_symbol_history {
  "country": "w1", "from_spec": "28.1", "to_spec": "28.2",
  "object_type": "codeunit", "object_name": "<real object>",
  "procedure_name": "<real procedure>", "granularity": "full"
}
  -> expect an ordered chain containing only real-change steps -- manually cross-check
     against `git log -- <path>` for the same range: the chain MUST be a subset of those
     commits, and shorter than it whenever the file has unrelated changes in range
```

## US3 — Query a completely different version end-to-end

Pick a (country, version) with no overlap with whatever is currently warm (e.g. a country
that has never been requested this session).

```
call bcatlas_request_version { "country": "<fresh country>", "spec": "<some version>" }
  -> expect status: "queued" or "in_progress" immediately, not a long hang

poll bcatlas_version_status { "country": "...", "commit_sha": "..." }
  -> expect "in_progress" while building, "ready" once done -- never a search/graph result
     before this flips to "ready"

call bcatlas_search { "query": "...", "country": "<fresh country>", "version": "<resolved>" }
call bcatlas_query_graph { "question": "...", "country": "<fresh country>", "version": "..." }
  -> expect real results grounded in that exact version -- spot-check one result's content
     against a known difference from the currently-warm w1-28 setup to confirm it's not
     silently serving the wrong version

Time a second request for a version in the SAME country, close to the first (e.g. the next
build), and compare wall-clock against the first cold build's time -- expect substantially
faster, demonstrating real incremental reuse (SC-006). Record the actual numbers observed;
do not assume the ~1%/~87% figures from research.md transfer exactly to wall-clock time.
```

## Eviction check (supporting SC-007/SC-008)

```
Drive warm residency past the configured disk budget (request several distinct, non-
overlapping (country, version) pairs in sequence).
  -> expect earlier, now-idle entries to be reclaimed automatically -- confirm via
     bcatlas_version_status returning to "unknown" for a reclaimed pair

Re-request a reclaimed (country, version).
  -> expect it becomes available again (rebuilt), not permanently failed
```
