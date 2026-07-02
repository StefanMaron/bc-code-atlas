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

cd "$ROOT/tools/graphify-al"
exec uv run python -m graphify.serve \
    "$ROOT/data/w1-28-src/graphify-out/graph.json" \
    --transport http \
    --host "${GRAPH_HOST:-127.0.0.1}" \
    --port "${GRAPH_PORT:-8802}"
