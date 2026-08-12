#!/usr/bin/env bash
# Graph backend: structural call/subscribe/extend graph for w1-28 (default :8802).
# Not meant to be exposed directly -- point testers at the aggregator instead
# (scripts/start-aggregator.sh), which forwards to this.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export GRAPHIFY_INSTRUCTIONS="${GRAPHIFY_INSTRUCTIONS:-$(cat <<'EOF'
A structural knowledge graph of Microsoft Dynamics 365 Business Central's AL
source (w1-28 base application) -- objects, procedures, event subscriptions,
and extension targets, with real call/subscribe/extend edges extracted from
source (not inferred/guessed).

Complements the semantic search server (the companion bc-code-atlas search
MCP server): search first to find a starting point by meaning, then use
query_graph/get_neighbors/shortest_path here to trace its exact connections
-- what calls or subscribes to a node, what it extends, or the shortest path
between two BC concepts.
EOF
)}"

# graphify-al's own safety cap on graph.json (guards against loading a
# runaway/corrupt file) defaults to 512MB. The default w1-28 corpus's real
# graph.json crossed that after the #26/#27 rebuild (~707MB -- global_id/
# al_owning_app on every AL node, plus ordinary upstream corpus growth since
# the last rebuild) and the service crash-looped on every restart until this
# was raised (confirmed live). 1GB leaves real headroom for continued
# organic growth without disabling the cap outright.
export GRAPHIFY_MAX_GRAPH_BYTES="${GRAPHIFY_MAX_GRAPH_BYTES:-1GB}"

cd "$ROOT/tools/graphify-al"
exec uv run --extra al python -m graphify.serve \
    "$ROOT/data/w1-28-src/graphify-out/graph.json" \
    --transport http \
    --host "${GRAPH_HOST:-127.0.0.1}" \
    --port "${GRAPH_PORT:-8802}"
