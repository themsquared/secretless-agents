#!/usr/bin/env bash
# A narrated walk through the same facts verify.sh asserts.
set -uo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
run() { printf '\n$ %s\n' "$*"; eval "$@"; }

say "1. What the agent container actually holds"
run "docker compose exec -T agent sh -c 'env | sort | grep -v ^PATH='"
echo "No provider key. If this process is compromised, there is no key to take."

say "2. What the agent asks for instead: a 60 second identity token"
run "docker compose exec -T agent sh -c \"python3 -c \\\"
import json,urllib.request
r=urllib.request.Request('http://issuer:8099/token',data=json.dumps({'agent':'research','ttl':60}).encode(),headers={'Content-Type':'application/json'})
b=json.load(urllib.request.urlopen(r)); print(b['token'][:60]+'...'); print('team:',b['team'],'ttl:',b['ttl_seconds'],'seconds')
\\\"\""

say "3. A call that works. The gateway attaches the provider key."
run "docker compose exec -T agent python3 /app/agent.py --agent research --model secretless-static"

say "4. The better variant: agentgateway mints a fresh upstream JWT per request"
run "docker compose exec -T agent python3 /app/agent.py --agent platform --model secretless-signed"

say "5. No token"
run "docker compose exec -T agent python3 /app/agent.py --no-token"

say "6. An expired token, minted 120 seconds in the past"
run "docker compose exec -T agent python3 /app/agent.py --ttl -120"
echo "At -30 this still succeeds: agentgateway allows 60s of clock skew on exp."


say "7. Valid identity, wrong model. Identity is not authorization."
run "docker compose exec -T agent python3 /app/agent.py --agent research --model secretless-signed"

say "8. The breach scenario: the agent's token, used against the provider directly"
run "docker compose exec -T agent python3 /app/agent.py --agent platform --direct"
echo "Everything the agent had is worth nothing outside the gateway, and expires in 60 seconds."
