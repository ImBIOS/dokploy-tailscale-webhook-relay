#!/usr/bin/env python3
"""Webhook relay - forwards GitHub webhooks and OAuth callbacks to Dokploy"""
import http.server
import urllib.request
import urllib.error
import json
import os

DOKPLOY_HOST = os.environ.get("DOKPLOY_HOST", "127.0.0.1")
DOKPLOY_PORT = int(os.environ.get("DOKPLOY_PORT", "3000"))
PORT = int(os.environ.get("RELAY_PORT", "8080"))

# Allowed paths (pass-through to Dokploy)
ALLOWED_PATHS = [
    "/api/deploy/github",           # Webhook events from GitHub
    "/api/providers/github/setup",  # OAuth callback from GitHub
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
        """Forward request to Dokploy if path is allowed."""
        if self.path == "/health":
            self.send_json_response(200, {"status": "ok"})
            return

        # Check if path is allowed (strip query string for matching)
        base_path = self.path.split("?")[0]
        if base_path not in ALLOWED_PATHS:
            self.send_json_response(404, {"error": "Not found"})
            return

        try:
            # Read request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else None

            # Build headers to forward
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
                # Pass through relevant headers
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
            print(f"[-] {method} {self.path} -> {e.code}")
        except Exception as e:
            self.send_json_response(500, {"error": str(e)})
            print(f"[-] {method} {self.path} -> Error: {e}")

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

if __name__ == "__main__":
    print(f"Starting webhook relay on :{PORT}")
    print(f"Allowed paths: {', '.join(ALLOWED_PATHS)}")
    print(f"Forwarding to http://{DOKPLOY_HOST}:{DOKPLOY_PORT}")
    server = http.server.HTTPServer(("0.0.0.0", PORT), WebhookRelay)
    server.serve_forever()
