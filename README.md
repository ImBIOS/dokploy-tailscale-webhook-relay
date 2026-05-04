# Dokploy Tailscale Webhook Relay

Expose Dokploy GitHub webhooks publicly while keeping the dashboard accessible only over Tailscale VPN.

## Problem

Dokploy deployed behind Tailscale cannot receive GitHub webhooks because GitHub's servers cannot reach your private Tailscale network. Exposing the entire server via Tailscale Funnel makes both the dashboard and webhooks public.

## Solution

A minimal webhook relay that:
- Keeps Dokploy dashboard private on Tailscale network
- Exposes only the webhook endpoints publicly via Cloudflare Tunnel
- Forwards requests transparently to Dokploy

![Architecture](architecture.png)

## Features

- **Dashboard Privacy**: Dokploy dashboard accessible only via Tailscale VPN
- **Public Webhooks**: GitHub webhooks reach Dokploy without exposing the dashboard
- **Minimal Setup**: Single Python script, no external dependencies
- **Cloudflare Tunnel**: Free, persistent tunnel for public access

## Quick Start

### Prerequisites

- Dokploy running on a server with Tailscale installed
- Cloudflare account (free) for named tunnels
- Domain added to Cloudflare (optional, for permanent URLs)

### 1. Install Cloudflare Tunnel

```bash
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared
```

### 2. Create Webhook Relay Service

Save as `/usr/local/bin/webhook-relay.py`:

```python
#!/usr/bin/env python3
"""Webhook relay - forwards GitHub webhooks to Dokploy"""
import http.server
import urllib.request
import urllib.error
import json
import os

DOKPLOY_HOST = os.environ.get("DOKPLOY_HOST", "127.0.0.1")
DOKPLOY_PORT = int(os.environ.get("DOKPLOY_PORT", "3000"))
PORT = int(os.environ.get("RELAY_PORT", "8080"))

ALLOWED_PATHS = [
    "/api/deploy/github",            # GitHub webhook events
    "/api/providers/github/setup",   # GitHub OAuth callback
]

class WebhookRelay(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json_response(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _forward(self, method):
        if self.path == "/health":
            self.send_json_response(200, {"status": "ok"})
            return

        base_path = self.path.split("?")[0]
        if base_path not in ALLOWED_PATHS:
            self.send_json_response(404, {"error": "Not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else None

            fwd_headers = {}
            for h in ["Content-Type", "X-GitHub-Event", "X-GitHub-Delivery",
                       "X-Hub-Signature-256", "X-Hub-Signature"]:
                val = self.headers.get(h)
                if val:
                    fwd_headers[h] = val

            req = urllib.request.Request(
                f"http://{DOKPLOY_HOST}:{DOKPLOY_PORT}{self.path}",
                data=body,
                headers=fwd_headers,
                method=method
            )

            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_body = resp.read()
                self.send_response(resp.code)
                for h in ["Content-Type", "Location"]:
                    val = resp.headers.get(h)
                    if val:
                        self.send_header(h, val)
                self.end_headers()
                self.wfile.write(resp_body)
                print(f"[+] {method} {self.path} -> {resp.code}")

        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else str(e)
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        except Exception as e:
            self.send_json_response(500, {"error": str(e)})

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

if __name__ == "__main__":
    print(f"Starting webhook relay on :{PORT}")
    server = http.server.HTTPServer(("0.0.0.0", PORT), WebhookRelay)
    server.serve_forever()
```

### 3. Create Systemd Service

Save as `/etc/systemd/system/webhook-relay.service`:

```ini
[Unit]
Description=Webhook Relay for GitHub
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 /usr/local/bin/webhook-relay.py
Restart=on-failure
RestartSec=5s
Environment="RELAY_PORT=8080"
Environment="DOKPLOY_HOST=127.0.0.1"
Environment="DOKPLOY_PORT=3000"

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now webhook-relay
```

### 4. Configure Cloudflare Tunnel

Create tunnel service at `/etc/systemd/system/cloudflared-webhook.service`:

```ini
[Unit]
Description=Cloudflare Tunnel for Webhooks
After=network.target webhook-relay.service

[Service]
Type=simple
ExecStart=/usr/local/bin/cloudflared tunnel --url http://localhost:8080
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared-webhook
```

### 5. Get Your Public URL

```bash
sudo journalctl -u cloudflared-webhook --no-pager -f | grep trycloudflare
```

You'll see something like:
```
Your quick Tunnel has been created. Visit it at: https://random-words.trycloudflare.com
```

### 6. Configure Dokploy Tailscale Access

```bash
# Ensure Tailscale Serve is configured for HTTPS
sudo tailscale serve --bg http://localhost:3000
```

Access Dokploy at `https://your-server.tailnet-name.ts.net/` (Tailscale only).

## GitHub App Configuration

In your GitHub App settings:

| Field | Value |
|-------|-------|
| **Callback URL** | `https://<your-tunnel-url>/api/providers/github/setup` |
| **Webhook URL** | `https://<your-tunnel-url>/api/deploy/github` |

## Permanent URL with Cloudflare Named Tunnel

For a permanent URL, create a Cloudflare Tunnel:

```bash
# Login to Cloudflare
cloudflared tunnel login

# Create tunnel
cloudflared tunnel create dokploy-webhook

# Configure DNS
cloudflared tunnel route dns dokploy-webhook webhook.yourdomain.com

# Run tunnel
cloudflared tunnel run --token <your-token>
```

Then update your GitHub App URLs to use `https://webhook.yourdomain.com`.

## Architecture

```
GitHub ──────────────────────► Cloudflare Tunnel ──► Webhook Relay ──► Dokploy
                                                                            ▲
                                                                            │
Tailscale Network ──► HTTPS ──► Dokploy Dashboard ◄────────────────────────┘
```

## Requirements Met

| Requirement | Solution |
|-------------|----------|
| Private dashboard | Tailscale Serve with HTTPS |
| Public webhooks | Cloudflare Tunnel + relay |
| GitHub integration | Transparent forwarding |
| Zero external deps | Pure Python stdlib |

## Contributing

Contributions welcome! Please open an issue or PR.

## License

MIT
