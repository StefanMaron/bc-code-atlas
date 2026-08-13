---
name: bc-code-atlas-cli
description: Query bc-code-atlas (semantic search + structural graph over Business Central AL base-application source and docs, across countries/versions) via a lightweight CLI instead of an MCP server entry. Use when you need to find how BC itself implements something, trace call/subscribe/extend relationships, or diff BC source across versions/countries.
---

# bc-code-atlas-cli — BC Code Atlas CLI

Thin CLI proxy over the [bc-code-atlas](https://github.com/StefanMaron/bc-code-atlas)
MCP server. Converts the "add an MCP server" pattern into a direct CLI call: no MCP
tool schema loaded into context, same underlying search/graph/registry/build
capability, compact text output instead of verbose JSON.

Use this instead of (not in addition to) an `bc-code-atlas` MCP server entry — pick
one connection method, not both.

## Install

Copy this directory (`skills/bc-code-atlas-cli/`) into wherever your coding agent
looks for skills (e.g. `~/.claude/skills/bc-code-atlas-cli/` for a user-wide install,
or `.claude/skills/bc-code-atlas-cli/` inside a specific project). Requires Node.js
(any version with global `fetch`, i.e. 18+). No dependencies to install — it's a
single script.

## Usage

```bash
node <skill-dir>/bc-code-atlas.js <command> [args...] [--flag value] [--json]
```

Run with `--help` for the full command list. All 18 upstream `bcatlas_*` tools are
covered, one subcommand each (dashes instead of underscores, `bcatlas_` prefix
dropped):

**Search & source**
- `search <query> [--limit N] [--offset N] [--include-tests true] [--country C] [--version SHA]` — semantic search over AL source + docs
- `get-signature <label>` / `get-procedure-body <label>` / `get-object-source <label>` — exact source re-read from disk, not the index

**Structural graph**
- `query-graph <question> [--mode bfs|dfs] [--depth N]` — broad BFS/DFS graph search
- `get-node <label>` / `resolve-node <object_type> <object_name> [--member M]` (deterministic — prefer this over `get-node` when you know the object type) / `get-neighbors <label> [--relation-filter R]`
- `get-community <community_id>` / `god-nodes [--top-n N]` / `graph-stats` / `shortest-path <source> <target>`

**Multi-version/country registry & build**
- `list-countries` / `list-versions <country>` / `resolve-version <country> <spec>`
- `request-version <country> <spec>` / `version-status <country> <commit_sha>` / `list-warm-versions [--country C]`
- `diff <country> <from_spec> <to_spec> [--path P | --object-type T --object-name N [--procedure-name P]]`
- `symbol-history <country> <from_spec> <to_spec> <object_type> <object_name> [--procedure-name P] [--granularity endpoints|full]`

`country`/`version` on search & graph commands are optional — omit both to use the
always-warm default `w1-28` corpus. `version` must be the exact `commit_sha` from
`resolve-version`/`request-version`, never `version_string`.

### Examples

```bash
node bc-code-atlas.js search "sales order posting validation" --limit 5
node bc-code-atlas.js get-object-source "Sales-Post"
node bc-code-atlas.js query-graph "what subscribes to OnBeforePostSalesDoc"
node bc-code-atlas.js list-countries
node bc-code-atlas.js diff w1 28.1 28.2 --object-type codeunit --object-name Sales-Post
```

Add `--json` to any command for the raw structured response instead of compact text.

## Pointing at a different instance

Defaults to the public hosted instance (`https://bc-code-atlas.stefanmaron.dev/mcp`).
To use your own self-hosted or federated deployment instead (see the project's
README Quick Start for running your own aggregator), set:

```bash
export BC_CODE_ATLAS_URL="https://your-instance.example.com/mcp"
# only needed if that instance is gated behind Cloudflare Access or similar:
export BC_CODE_ATLAS_CF_ACCESS_CLIENT_ID="..."
export BC_CODE_ATLAS_CF_ACCESS_CLIENT_SECRET="..."
```

## Notes

- Each invocation does a fresh MCP `initialize` handshake then one `tools/call` —
  stateless, safe to call repeatedly, no persistent session to manage.
- This is a thin wrap of an existing MCP server, not a separate implementation — the
  tool list, argument names, and behavior are 1:1 with the upstream
  `aggregator/unified_mcp_server.py` in the bc-code-atlas source repo. If a tool's
  arguments change upstream, this CLI's `COMMANDS` table needs a matching update.
- If output degrades to raw JSON dumps unexpectedly, check `printCompact()` in
  `bc-code-atlas.js`.
