#!/usr/bin/env node
// Thin CLI over the bc-code-atlas MCP server -- an alternative to wiring up
// bc-code-atlas as a live MCP server entry: no MCP tool schema loaded into
// an agent's context per spawn, one JSON-RPC initialize + tools/call per
// invocation, compact text output instead of verbose JSON by default.
//
// Defaults to the public hosted instance. Point it at a self-hosted or
// federated instance instead by setting BC_CODE_ATLAS_URL (see README.md's
// Quick Start for running your own aggregator locally).

const ENDPOINT = process.env.BC_CODE_ATLAS_URL || 'https://bc-code-atlas.stefanmaron.dev/mcp';

// Optional -- only needed if the target instance is gated behind Cloudflare
// Access or similar (a private/self-hosted deployment might be). The public
// hosted instance needs neither.
const CF_ACCESS_CLIENT_ID = process.env.BC_CODE_ATLAS_CF_ACCESS_CLIENT_ID;
const CF_ACCESS_CLIENT_SECRET = process.env.BC_CODE_ATLAS_CF_ACCESS_CLIENT_SECRET;

async function mcpRequest(session, method, params) {
  const body = { jsonrpc: '2.0', id: Date.now(), method, params };
  const headers = {
    'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream',
  };
  if (CF_ACCESS_CLIENT_ID) headers['CF-Access-Client-Id'] = CF_ACCESS_CLIENT_ID;
  if (CF_ACCESS_CLIENT_SECRET) headers['CF-Access-Client-Secret'] = CF_ACCESS_CLIENT_SECRET;
  if (session.id) headers['Mcp-Session-Id'] = session.id;

  const res = await fetch(ENDPOINT, { method: 'POST', headers, body: JSON.stringify(body) });

  const sid = res.headers.get('mcp-session-id');
  if (sid) session.id = sid;

  const text = await res.text();
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 500)}`);
  }

  // Response may be plain JSON or an SSE stream ("event: message\ndata: {...}\n\n")
  const jsonLine = text.split('\n').find((l) => l.startsWith('data:'));
  const raw = jsonLine ? jsonLine.slice(5).trim() : text.trim();
  if (!raw) return null;
  return JSON.parse(raw);
}

async function initSession() {
  const session = {};
  await mcpRequest(session, 'initialize', {
    protocolVersion: '2025-06-18',
    capabilities: {},
    clientInfo: { name: 'bc-code-atlas-cli', version: '0.2.0' },
  });
  return session;
}

async function callTool(name, args) {
  const session = await initSession();
  const result = await mcpRequest(session, 'tools/call', { name, arguments: args });
  if (result?.error) throw new Error(result.error.message || JSON.stringify(result.error));
  return result?.result;
}

function printCompact(result) {
  const structured = result?.structuredContent;
  if (structured !== undefined) {
    console.log(typeof structured === 'string' ? structured : JSON.stringify(structured, null, 2));
    return;
  }
  const content = result?.content;
  if (Array.isArray(content)) {
    for (const block of content) {
      if (block.type !== 'text') {
        console.log(JSON.stringify(block, null, 2));
        continue;
      }
      try {
        const parsed = JSON.parse(block.text);
        console.log(JSON.stringify(parsed, null, 2));
      } catch {
        console.log(block.text);
      }
    }
    return;
  }
  console.log(JSON.stringify(result, null, 2));
}

// name -> { tool, positional: [argName...], optional: [argName...] }
const COMMANDS = {
  search: {
    tool: 'bcatlas_search',
    positional: ['query'],
    optional: ['limit', 'offset', 'refresh_index', 'languages', 'paths', 'include_tests', 'country', 'version'],
  },
  'query-graph': {
    tool: 'bcatlas_query_graph',
    positional: ['question'],
    optional: ['mode', 'depth', 'token_budget', 'context_filter', 'country', 'version'],
  },
  'get-node': { tool: 'bcatlas_get_node', positional: ['label'], optional: ['country', 'version'] },
  'resolve-node': {
    tool: 'bcatlas_resolve_node',
    positional: ['object_type', 'object_name'],
    optional: ['member', 'limit', 'country', 'version'],
  },
  'get-neighbors': {
    tool: 'bcatlas_get_neighbors',
    positional: ['label'],
    optional: ['relation_filter', 'country', 'version'],
  },
  'get-signature': { tool: 'bcatlas_get_signature', positional: ['label'], optional: ['country', 'version'] },
  'get-procedure-body': {
    tool: 'bcatlas_get_procedure_body',
    positional: ['label'],
    optional: ['country', 'version'],
  },
  'get-object-source': {
    tool: 'bcatlas_get_object_source',
    positional: ['label'],
    optional: ['country', 'version'],
  },
  'get-community': { tool: 'bcatlas_get_community', positional: ['community_id'], optional: ['country', 'version'] },
  'god-nodes': { tool: 'bcatlas_god_nodes', positional: [], optional: ['top_n', 'country', 'version'] },
  'graph-stats': { tool: 'bcatlas_graph_stats', positional: [], optional: ['country', 'version'] },
  'shortest-path': {
    tool: 'bcatlas_shortest_path',
    positional: ['source', 'target'],
    optional: ['max_hops', 'country', 'version'],
  },
  'list-countries': { tool: 'bcatlas_list_countries', positional: [], optional: [] },
  'list-versions': { tool: 'bcatlas_list_versions', positional: ['country'], optional: [] },
  'resolve-version': { tool: 'bcatlas_resolve_version', positional: ['country', 'spec'], optional: [] },
  'request-version': { tool: 'bcatlas_request_version', positional: ['country', 'spec'], optional: [] },
  'version-status': { tool: 'bcatlas_version_status', positional: ['country', 'commit_sha'], optional: [] },
  'list-warm-versions': { tool: 'bcatlas_list_warm_versions', positional: [], optional: ['country'] },
  diff: {
    tool: 'bcatlas_diff',
    positional: ['country', 'from_spec', 'to_spec'],
    optional: ['path', 'object_type', 'object_name', 'procedure_name'],
  },
  'symbol-history': {
    tool: 'bcatlas_symbol_history',
    positional: ['country', 'from_spec', 'to_spec', 'object_type', 'object_name'],
    optional: ['procedure_name', 'granularity'],
  },
};

function coerce(val) {
  if (val === 'true') return true;
  if (val === 'false') return false;
  if (/^-?\d+$/.test(val)) return parseInt(val, 10);
  if (val.includes(',') && !val.includes(' ')) return val.split(',');
  return val;
}

function parseArgs(spec, rest) {
  const args = {};
  const positionalVals = [];
  for (let i = 0; i < rest.length; i++) {
    const tok = rest[i];
    if (tok.startsWith('--')) {
      const key = tok.slice(2).replace(/-/g, '_');
      const val = rest[++i];
      if (val === undefined) throw new Error(`--${tok.slice(2)} requires a value`);
      args[key] = coerce(val);
    } else {
      positionalVals.push(tok);
    }
  }
  spec.positional.forEach((name, idx) => {
    if (positionalVals[idx] === undefined) throw new Error(`missing required argument: ${name}`);
    args[name] = coerce(positionalVals[idx]);
  });
  return args;
}

function printHelp() {
  console.log(`bc-code-atlas - CLI for the bc-code-atlas MCP server (${ENDPOINT})

Usage: bc-code-atlas <command> [positional args...] [--flag value ...] [--json]

Commands:
  search <query> [--limit N] [--offset N] [--include-tests true] [--country C] [--version SHA]
  query-graph <question> [--mode bfs|dfs] [--depth N] [--country C] [--version SHA]
  get-node <label> [--country C] [--version SHA]
  resolve-node <object_type> <object_name> [--member M] [--limit N] [--country C] [--version SHA]
  get-neighbors <label> [--relation-filter R] [--country C] [--version SHA]
  get-signature <label> [--country C] [--version SHA]
  get-procedure-body <label> [--country C] [--version SHA]
  get-object-source <label> [--country C] [--version SHA]
  get-community <community_id> [--country C] [--version SHA]
  god-nodes [--top-n N] [--country C] [--version SHA]
  graph-stats [--country C] [--version SHA]
  shortest-path <source> <target> [--max-hops N] [--country C] [--version SHA]
  list-countries
  list-versions <country>
  resolve-version <country> <spec>
  request-version <country> <spec>
  version-status <country> <commit_sha>
  list-warm-versions [--country C]
  diff <country> <from_spec> <to_spec> [--path P | --object-type T --object-name N [--procedure-name P]]
  symbol-history <country> <from_spec> <to_spec> <object_type> <object_name> [--procedure-name P] [--granularity endpoints|full]

Add --json to any command for the raw structured response instead of compact text.

Env vars:
  BC_CODE_ATLAS_URL                    override the endpoint (default: the public hosted instance)
  BC_CODE_ATLAS_CF_ACCESS_CLIENT_ID     Cloudflare Access service token, if the target instance is gated
  BC_CODE_ATLAS_CF_ACCESS_CLIENT_SECRET
`);
}

async function main() {
  const [cmd, ...rest] = process.argv.slice(2);
  const asJson = rest.includes('--json');
  const filtered = rest.filter((a) => a !== '--json');

  if (!cmd || cmd === '--help' || cmd === '-h') {
    printHelp();
    process.exit(cmd ? 0 : 1);
  }

  const spec = COMMANDS[cmd];
  if (!spec) {
    console.error(`Error: unknown command "${cmd}". Run with --help for the list.`);
    process.exit(1);
  }

  try {
    const args = parseArgs(spec, filtered);
    const result = await callTool(spec.tool, args);
    if (asJson) console.log(JSON.stringify(result, null, 2));
    else printCompact(result);
  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  }
}

main();
