# Exposing bc-code-atlas via Cloudflare Tunnel

Only the aggregator (`:8800`) ever needs to be reachable from outside your
machine. The search and graph backends stay bound to `127.0.0.1` — the
tunnel never touches them directly, and neither should anyone else. This
also means the tunnel is the *only* thing standing between the internet and
your MCP tools, so it's gated with Cloudflare Access rather than left open.

Requires a domain already onboarded to Cloudflare (free tier is fine).

## 1. Install `cloudflared`

```bash
# Arch
sudo pacman -S cloudflared
# Debian/Ubuntu
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
# macOS
brew install cloudflared
```

## 2. Authenticate (interactive — opens a browser)

```bash
cloudflared tunnel login
```

Pick the domain you want to use when the browser prompts.

## 3. Create a named tunnel

```bash
cloudflared tunnel create bc-code-atlas
```

This writes credentials to `~/.cloudflared/<tunnel-id>.json` and prints the
tunnel ID — you'll need it below.

## 4. Write the ingress config

Create `~/.cloudflared/config.yml` (adjust `tunnel`, `credentials-file`, and
the hostname to your own):

```yaml
tunnel: bc-code-atlas
credentials-file: /home/<you>/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: bc-code-atlas.yourdomain.com
    service: http://localhost:8800
  - service: http_status:404
```

A single rule routing straight to the aggregator — no path-based splitting
needed, since the aggregator already unifies both backends onto one `/mcp`
endpoint.

## 5. Create the DNS record

```bash
cloudflared tunnel route dns bc-code-atlas bc-code-atlas.yourdomain.com
```

## 6. Gate it with Cloudflare Access

In the Cloudflare dashboard: **Zero Trust → Access → Applications → Add an
application → Self-hosted**.

- Application domain: `bc-code-atlas.yourdomain.com`
- Policy: start with **email one-time PIN**, allow-list the specific email
  addresses of people you've actually invited to test. Tighten or loosen
  later — this is just gating who can reach the tunnel at all, not
  per-tool permissions.

This puts an Access login page in front of the MCP endpoint. MCP clients
that support HTTP auth headers can use a
[service token](https://developers.cloudflare.com/cloudflare-one/identity/service-tokens/)
instead of interactive login if a tester's client can't handle a browser
redirect.

## 7. Run it

```bash
# make sure the three local servers are already running first:
#   ./scripts/start-search-server.sh
#   ./scripts/start-graph-server.sh
#   ./scripts/start-aggregator.sh

cloudflared tunnel run bc-code-atlas
```

For a longer-lived setup, install it as a system service instead of running
it in a terminal:

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

## 8. Verify

- Visit `https://bc-code-atlas.yourdomain.com` in a browser — you should hit
  the Cloudflare Access login page, *not* a raw MCP error. If you land
  straight on an MCP response, the Access policy isn't applied yet.
- After authenticating, point an MCP client's `.mcp.json` at
  `https://bc-code-atlas.yourdomain.com/mcp` and confirm a `search` or
  `query_graph` call round-trips.
- From a machine *not* on your Access allow-list, confirm the same URL is
  blocked.

## Optional: extra defense-in-depth

`graphify-al`'s HTTP transport supports its own bearer-token gate
independent of Cloudflare Access — `--api-key`/`GRAPHIFY_API_KEY` on
`scripts/start-graph-server.sh`. Since that backend is never directly
tunneled (only the aggregator is), this is optional belt-and-suspenders,
not required for the setup above to be safe.
