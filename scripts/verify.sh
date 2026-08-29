#!/usr/bin/env bash
# Assertions, not narration. Every check below fails loudly if the
# credential boundary is not where the README says it is.
set -uo pipefail
cd "$(dirname "$0")/.."

pass=0
fail=0

check() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$actual" == *"$expected"* ]]; then
    printf 'PASS  %s\n' "$name"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s\n      expected to contain: %s\n      got: %s\n' \
      "$name" "$expected" "$actual"
    fail=$((fail + 1))
  fi
}

agent() {
  docker compose exec -T agent python3 /app/agent.py "$@" 2>&1
}

echo "== the agent holds nothing worth stealing =="

env_secrets=$(docker compose exec -T agent sh -c \
  'env | grep -iE "api_key|secret|token|password" || echo NONE')
check "agent environment has no credential" "NONE" "$env_secrets"

files=$(docker compose exec -T agent sh -c \
  'grep -rl "sk-upstream" /app /etc /root /run/secrets 2>/dev/null || echo NONE')
check "agent filesystem has no provider key" "NONE" "$files"

echo
echo "== the gateway attaches what the agent does not have =="

out=$(agent --agent research --model secretless-static)
check "research agent reaches the model" "HTTP 200" "$out"
check "upstream saw the provider key" "long-lived provider key" "$out"

out=$(agent --agent platform --model secretless-signed)
check "platform agent reaches the signed-JWT model" "HTTP 200" "$out"
check "upstream saw a per-request token" "agentgateway minted for this request" "$out"
check "that token lives 60 seconds" "expires_in=60s" "$out"

echo
echo "== identity is enforced =="

out=$(agent --no-token)
check "no token is rejected" "HTTP 401" "$out"

out=$(agent --ttl -120)
check "expired token is rejected" "HTTP 401" "$out"

# Not a bug, and worth asserting so nobody is surprised in production:
# agentgateway allows 60 seconds of clock skew on exp.
out=$(agent --ttl -30)
check "expiry has 60s of clock-skew leeway" "HTTP 200" "$out"

forged=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  http://localhost:3300/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-upstream-do-not-leak' \
  -d '{"model":"secretless-static","messages":[{"role":"user","content":"hi"}]}')
check "the provider key itself is not a gateway credential" "401" "$forged"

echo
echo "== authorization is separate from identity =="

out=$(agent --agent research --model secretless-signed)
check "research is denied the model it may not use" "HTTP 403" "$out"

echo
echo "== the agent's token is worthless to the provider =="

out=$(agent --agent platform --direct)
check "going around the gateway fails" "HTTP 401" "$out"
check "the provider says why" "wrong or missing provider key" "$out"

echo
printf '%d passed, %d failed\n' "$pass" "$fail"
[[ "$fail" -eq 0 ]]
