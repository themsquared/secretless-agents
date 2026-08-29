"""The agent. Note what is not in this file: a provider API key.

It has no secret in its environment, no key file mounted, nothing on disk
worth stealing. To call a model it asks the local issuer for an identity
token that lives 60 seconds, sends that to agentgateway, and the gateway
decides what it is allowed to do and attaches the real provider credential
on the way out.

Usage:
  python3 agent.py                       research agent, default model
  python3 agent.py --agent platform --model gpt-4o
  python3 agent.py --ttl -30             mint an already-expired token
  python3 agent.py --no-token            call the gateway anonymously
  python3 agent.py --direct              skip the gateway, call the provider
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ISSUER_URL = os.environ.get("ISSUER_URL", "http://issuer:8099")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://agentgateway:3300")
UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "http://mock-llm:8088")


def post(url, payload, token=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return err.code, {"raw": raw.decode(errors="replace")}


def get_token(agent, ttl):
    status, body = post(f"{ISSUER_URL}/token", {"agent": agent, "ttl": ttl})
    if status != 200:
        print(f"issuer refused to mint a token: {status} {body}")
        sys.exit(1)
    return body["token"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="research")
    ap.add_argument("--model", default="secretless-static")
    ap.add_argument("--ttl", type=int, default=60)
    ap.add_argument("--no-token", action="store_true")
    ap.add_argument("--direct", action="store_true")
    ap.add_argument("--prompt", default="Say hello.")
    args = ap.parse_args()

    payload = {"model": args.model,
               "messages": [{"role": "user", "content": args.prompt}]}

    token = None if args.no_token else get_token(args.agent, args.ttl)

    if args.direct:
        # The agent's token is an identity assertion for the gateway. It is
        # not a provider credential, so going around the gateway fails.
        url = f"{UPSTREAM_URL}/static/v1/chat/completions"
    else:
        url = f"{GATEWAY_URL}/v1/chat/completions"

    status, body = post(url, payload, token)
    print(f"HTTP {status}")
    choices = body.get("choices")
    if status == 200 and choices:
        print(choices[0]["message"]["content"])
    else:
        print(json.dumps(body, indent=2))
    sys.exit(0 if status == 200 else 2)


if __name__ == "__main__":
    main()
