"""A minimal identity issuer for agent workloads.

Mints short-lived RS256 tokens that say who an agent is, and nothing else.
There is no provider API key anywhere in this service. In a real deployment
this role is played by SPIFFE/SPIRE, an OIDC provider, or the cloud's
workload identity service.

  GET  /.well-known/jwks.json   public keys, for agentgateway to validate with
  POST /token                   {"agent": "research", "ttl": 60} -> {"token": "..."}

A negative ttl is allowed on purpose so the demo can mint an already-expired
token and show what the gateway does with it.
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt

ISSUER = os.environ.get("ISSUER_NAME", "https://issuer.secretless.local")
AUDIENCE = os.environ.get("ISSUER_AUDIENCE", "agentgateway")
PORT = int(os.environ.get("ISSUER_PORT", "8099"))
KEYS = "/keys"

# The only thing the issuer knows about an agent: which team it belongs to.
# agentgateway turns that claim into an authorization decision.
AGENTS = {
    "research": "research",
    "platform": "platform",
    "contractor": "contractors",
}

with open(f"{KEYS}/issuer.key", "rb") as f:
    SIGNING_KEY = f.read()
with open(f"{KEYS}/jwks.json", "rb") as f:
    JWKS = f.read()


def mint(agent: str, ttl: int) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": f"agent/{agent}",
        "team": AGENTS[agent],
        "iat": now,
        "nbf": now - 5,
        "exp": now + ttl,
    }
    return jwt.encode(claims, SIGNING_KEY, algorithm="RS256",
                      headers={"kid": "issuer-1"})


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json"):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.startswith("/.well-known/jwks.json"):
            self._send(200, JWKS)
        elif self.path.startswith("/healthz"):
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/token"):
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid json"})
            return
        agent = req.get("agent", "research")
        if agent not in AGENTS:
            self._send(400, {"error": f"unknown agent {agent}",
                             "known": sorted(AGENTS)})
            return
        ttl = int(req.get("ttl", 60))
        self._send(200, {"token": mint(agent, ttl), "agent": agent,
                         "team": AGENTS[agent], "ttl_seconds": ttl})

    def log_message(self, fmt, *args):
        print("issuer: " + fmt % args, flush=True)


if __name__ == "__main__":
    print(f"issuer: listening on :{PORT} as {ISSUER}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
