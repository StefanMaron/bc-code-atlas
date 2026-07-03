# Exposing bc-code-atlas via Cloudflare Tunnel

Only the aggregator (`:8800`) ever needs to be reachable from outside your
machine. The search and graph backends stay bound to `127.0.0.1` — the
tunnel never touches them directly, and neither should anyone else. This
also means the tunnel is the *only* thing standing between the internet and
your MCP tools, so it's gated with Cloudflare Access rather than left open.

Requires a domain already onboarded to Cloudflare (free tier is fine).

Two ways to run the tunnel — pick one:

- **Docker + dashboard-managed tunnel** (below) — create the tunnel in the
  Zero Trust dashboard, run `cloudflared` in a container with the token it
  gives you. No local `cloudflared` install, hostname routing is configured
  in the dashboard instead of a local file.
- **CLI-managed tunnel** (see [Alternative](#alternative-cli-managed-tunnel)
  below) — install `cloudflared` locally, `login`/`create` from the CLI,
  route via a local `config.yml`. Prefer this if you don't want a Docker
  dependency, or want the routing config to live in a file you control.

## Docker + dashboard-managed tunnel

### 1. Create the tunnel in the dashboard

Zero Trust → **Networks → Tunnels → Create a tunnel → Cloudflared** → name
it (e.g. `bc-code-atlas`) → choose the Docker install option. The dashboard
shows a `docker run ... --token eyJ...` command — **don't run it as shown**;
the token is a live credential and shouldn't end up in your shell history.
Copy just the token value and store it in 1Password instead (per this repo
owner's secrets-handling convention — adapt the vault name if you're
following along yourself):

```bash
op item create --category="API Credential" --vault=claude \
  --title="bc-code-atlas cloudflare tunnel token" \
  credential=<paste-the-token>
```

If a token was ever pasted into a chat, terminal share, or anywhere else
outside a password manager, rotate it first (same Tunnels page → Configure →
refresh the token) before storing — treat an exposed token as compromised.

### 2. Run the tunnel container, pulling the token from 1Password at runtime

```bash
op run --env-file=<(echo 'TUNNEL_TOKEN=op://claude/bc-code-atlas cloudflare tunnel token/credential') -- \
  docker run -d --network host --name bc-code-atlas-tunnel --restart unless-stopped \
  -e TUNNEL_TOKEN \
  cloudflare/cloudflared:latest tunnel --no-autoupdate run
```

- `--network host` (Linux) is required — without it, `localhost:8800` inside
  the container refers to the container itself, not your machine, so
  cloudflared can never reach the aggregator. (macOS/Windows Docker Desktop:
  use `host.docker.internal:8800` as the public-hostname service URL instead
  of `localhost:8800`, and drop `--network host`.)
- `-e TUNNEL_TOKEN` (no `=value`) forwards the variable from the invoking
  process's environment — which `op run` just set — into the container. The
  token never appears as literal text in the command, shell history, or `ps`.
- `cloudflared tunnel run` (no `--token` flag) picks up `TUNNEL_TOKEN` from
  its environment automatically.
- `--restart unless-stopped` keeps it running across reboots without a
  separate systemd unit.

### 3. Route the hostname to the aggregator

Same dashboard page, **Public Hostname** tab → Add a public hostname:

- Subdomain/domain: whatever you want testers to use
  (e.g. `bc-code-atlas.yourdomain.com`)
- Service: **HTTP**, URL `localhost:8800`

### 4. Allow-list the public hostname on the aggregator itself

The MCP transport's DNS-rebinding protection only accepts
`Host: localhost`/`127.0.0.1` by default. A tunnel forwards the original
public `Host` header unchanged, so without this every request gets rejected
with `Invalid Host header` (Cloudflare surfaces it as a `421`) before it
even reaches Access or a tool — found the hard way while validating this
exact setup. Set the hostname when starting the aggregator:

```bash
export AGGREGATOR_PUBLIC_HOSTNAME="bc-code-atlas.yourdomain.com"
./scripts/start-aggregator.sh
```

## Gate it with Cloudflare Access

Email OTP / interactive login **does not work here** — MCP clients can't
follow a browser login redirect, so they'd just get stuck. Use a
**Service Token** instead: header-based machine-to-machine auth that any
MCP client supporting custom HTTP headers can attach directly, no browser
involved.

1. **Zero Trust → Access → Service Auth → Service Tokens → Create Service
   Token.** Name it (e.g. `bc-code-atlas-tester-1` — one per tester gives
   you an individually revocable audit trail; a shared token is simpler if
   the group is small and trusted). Cloudflare shows a **Client ID** and
   **Client Secret** exactly once — store both immediately in a password
   manager, never in a file or shell history.
2. **Zero Trust → Access → Applications** → the application for your
   hostname → **Policies** → add one with action **Service Auth** and
   include rule **Valid Service Token** → select the token from step 1.
3. Testers add the headers to their MCP client config. For Claude Code's
   `.mcp.json`:
   ```json
   {
     "mcpServers": {
       "bc-code-atlas": {
         "type": "http",
         "url": "https://bc-code-atlas.yourdomain.com/mcp",
         "headers": {
           "CF-Access-Client-Id": "<client-id>",
           "CF-Access-Client-Secret": "<client-secret>"
         }
       }
     }
   }
   ```

## Verify

- An unauthenticated request should get a hard block, not a real response:
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" https://<hostname>/mcp \
    -X POST -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
  # expect 403, not 200 -- if you get 200 here, the Access policy isn't attached/enforcing yet
  ```
- The same request with `-H "CF-Access-Client-Id: ..." -H "CF-Access-Client-Secret: ..."` should return `200` with a real MCP `initialize` response.
- A wrong/garbage secret should still `403`.

## Known limitation: restarting a backend breaks whoever is connected at that instant

Restarting the aggregator, or any backend it's actively forwarding a call
to (search/graph/registry/build), kills the OS-level connection(s)
cloudflared has open to it. Any request genuinely in-flight at that exact
moment gets an abrupt, truncated response instead of a clean error --
confirmed live (not theorized) on 2026-07-03: an aggregator restart at the
exact process-start timestamp `10:07:13 UTC` lined up second-for-second
with a burst of `unexpected EOF` errors in `docker logs bc-code-atlas-tunnel`
against `originService=http://localhost:8800`, and a real external tester
saw a client-side crash (`Cannot read properties of undefined (reading
'invoke')`) at that moment and had to restart their own MCP client to
recover -- their client's session/connection to the aggregator didn't
self-heal on its own, even though the tunnel container did.

What does and doesn't need restarting after this happens:
- **The `bc-code-atlas-tunnel` container itself never needs restarting.**
  Its outbound connection pool to `localhost:8800` just dials a fresh
  connection on the next request -- confirmed by real external traffic
  succeeding immediately after a backend restart with zero action taken
  on the tunnel container.
- **The affected MCP client does need to reconnect** (restart the client,
  or whatever forces it to re-`initialize` a fresh session) -- its
  in-flight request/session was the thing that actually broke.

There's currently no zero-downtime restart path for any of these
processes (search, graph, registry, build, aggregator) -- a restart is a
plain kill-and-relaunch (see `scripts/start-*.sh`), so this is a real,
open operational gap, not just a one-off fluke. Logged as a possible
future fix in `IDEAS.md`. Until/unless that exists, avoid restarting
during a window when you know a tester is actively connected, and expect
that anyone who is connected at the moment of a restart will need to
reconnect their client afterward.

## Optional: extra defense-in-depth

`graphify-al`'s HTTP transport supports its own bearer-token gate
independent of Cloudflare Access — `--api-key`/`GRAPHIFY_API_KEY` on
`scripts/start-graph-server.sh`. Since that backend is never directly
tunneled (only the aggregator is), this is optional belt-and-suspenders,
not required for the setup above to be safe.

## Alternative: CLI-managed tunnel

If you'd rather not depend on Docker:

```bash
# 1. Install
sudo pacman -S cloudflared          # Arch
# or: curl -L --output cloudflared.deb \
#   https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
#   && sudo dpkg -i cloudflared.deb   # Debian/Ubuntu
# or: brew install cloudflared        # macOS

# 2. Authenticate (interactive -- opens a browser, pick your domain)
cloudflared tunnel login

# 3. Create a named tunnel
cloudflared tunnel create bc-code-atlas
# writes ~/.cloudflared/<tunnel-id>.json, prints the tunnel ID

# 4. Create the DNS record
cloudflared tunnel route dns bc-code-atlas bc-code-atlas.yourdomain.com
```

Write `~/.cloudflared/config.yml`:

```yaml
tunnel: bc-code-atlas
credentials-file: /home/<you>/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: bc-code-atlas.yourdomain.com
    service: http://localhost:8800
  - service: http_status:404
```

Then run it (make sure the three local servers are already up first):

```bash
cloudflared tunnel run bc-code-atlas
```

Or install as a system service for a longer-lived setup:

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

The Access-gate and Verify steps above apply the same way regardless of
which path you used to stand up the tunnel.
