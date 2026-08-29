"""A stand-in LLM provider that actually checks its credential.

Two endpoints, two upstream auth styles, so the demo can prove both halves
of the pattern without spending real money on a provider:

  /static/v1/chat/completions   requires the long-lived provider key in
                                Authorization. Only agentgateway has it.
  /signed/v1/chat/completions   requires a fresh RS256 JWT signed by the
                                gateway's private key. agentgateway mints
                                one per request (backendAuth jwtSign).

Both return 401 when the credential is missing or wrong, which is what makes
the demo an assertion instead of a claim: if the agent's request reaches an
LLM at all, the gateway must have attached something the agent never held.

The answer text reports how the request authenticated, so a curl of the demo
shows the mechanism rather than describing it.
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt

PORT = int(os.environ.get("MOCK_PORT", "8088"))
UPSTREAM_KEY = os.environ["UPSTREAM_API_KEY"]
SIGNED_ISSUER = os.environ.get("SIGNED_ISSUER", "agentgateway")
SIGNED_AUDIENCE = os.environ.get("SIGNED_AUDIENCE", "mock-llm")

with open("/keys/gateway-sign.pub", "rb") as f:
    GATEWAY_PUB = f.read()


def completion(model: str, note: str):
    return {
        "id": "chatcmpl-secretless",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": note},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 20,
                  "total_tokens": 40},
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _bearer(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        return header[len("Bearer "):].strip()

    def _check_static(self):
        token = self._bearer()
        if token != UPSTREAM_KEY:
            self._send(401, {"error": {
                "message": "upstream rejected the request: wrong or missing provider key",
                "type": "invalid_request_error"}})
            return None
        return "authenticated with the long-lived provider key, held only by agentgateway"

    def _check_signed(self):
        token = self._bearer()
        if not token:
            self._send(401, {"error": {
                "message": "upstream rejected the request: no signed token",
                "type": "invalid_request_error"}})
            return None
        try:
            claims = jwt.decode(token, GATEWAY_PUB, algorithms=["RS256"],
                                audience=SIGNED_AUDIENCE, issuer=SIGNED_ISSUER)
        except jwt.PyJWTError as err:
            self._send(401, {"error": {
                "message": f"upstream rejected the signed token: {err}",
                "type": "invalid_request_error"}})
            return None
        now = int(time.time())
        print(f"mock-llm: signed token iat={claims['iat']} exp={claims['exp']} "
              f"now={now}", flush=True)
        return (f"authenticated with a JWT agentgateway minted for this request, "
                f"sub={claims.get('sub')} expires_in={claims['exp'] - now}s")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) or b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        model = body.get("model", "unknown")

        if self.path.startswith("/static/"):
            note = self._check_static()
        elif self.path.startswith("/signed/"):
            note = self._check_signed()
        else:
            self._send(404, {"error": {"message": f"no such path {self.path}"}})
            return

        if note is None:
            return
        self._send(200, completion(model, note))

    def do_GET(self):
        if self.path.endswith("/healthz"):
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def log_message(self, fmt, *args):
        print("mock-llm: " + fmt % args, flush=True)


if __name__ == "__main__":
    print(f"mock-llm: listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
