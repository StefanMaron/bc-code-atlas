"""bc-code-atlas build service: bounded-concurrency build queue, clone-
nearest-warm-sibling incremental builds, staging + atomic promote, and
LRU/TTL eviction of warm (country, version) artifacts under a disk budget.

Served as its own MCP-over-HTTP server (build/build/mcp_server.py, a later
task), proxied through the aggregator like every other bc-code-atlas
capability (constitution Principle I). Building (GPU-bound) and serving
(CPU-bound, in chunker/ and tools/graphify-al) are deliberately separate
resource pools (constitution Principle II) -- this project only ever writes
to staging and promotes via atomic rename, never opens a served artifact for
writing.
"""
