# secretless-agents

> 📖 **Read the write-up:** [Your AI Agent Should Not Hold the LLM API Key](https://webofmike.com/secretless-ai-agents/)

An agent that calls an LLM without ever holding an LLM credential.

The agent process has no API key in its environment, no key file on its
filesystem, and nothing in its dependency tree that could exfiltrate one. To
call a model it asks a local issuer for an identity token that expires in 60
seconds, sends that to [agentgateway](https://agentgateway.dev), and the
gateway decides what it may do and attaches the real provider credential on
the way out.

Everything runs locally with `docker compose`. There is no cloud account and
no provider key to supply.

## Why this shape

The March 2026 compromise of the `litellm` PyPI package is the argument. A
maintainer's GitHub account was taken over and the release pipeline used to
publish versions 1.82.7 and 1.82.8 carrying a credential stealer. The packages
were live for roughly 40 minutes and were downloaded tens of thousands of
times.

Two details are why this demo exists. The payload shipped as a `.pth` file,
which CPython executes on interpreter startup, so it ran without anyone
writing `import litellm`. And what it harvested was environment variables,
`.env` files, cloud credentials, and SSH keys: exactly the places an LLM
provider API key lives.

That key does not expire, is not scoped to a model, is not scoped to a tenant,
and its theft is invisible until the bill arrives. Reviewing your dependencies
would not have helped here, and neither would a better secret store. The
credential simply should not have been in that process.

Background: [Sonatype's analysis](https://www.sonatype.com/blog/compromised-litellm-pypi-package-delivers-multi-stage-credential-stealer)
and [InfoQ's writeup](https://www.infoq.com/news/2026/03/litellm-supply-chain-attack/).

Two boundaries do the work:

1. **Inbound.** The agent authenticates to the gateway with a short-lived
   identity token from a workload identity system. In this demo that is a
   60-second RS256 JWT from a local issuer. In a cluster it is SPIFFE, an
   OIDC provider, or the cloud's workload identity service.
2. **Outbound.** The gateway holds the provider credential and attaches it.
   For one model here it sends a long-lived key; for the other, agentgateway
   mints a fresh signed JWT per request, so nothing long-lived exists on the
   wire at all.

What a compromised agent gets is then bounded by design: a 60-second token,
scoped to one team, usable only against the gateway, that the provider itself
will reject.

## Architecture

```
agent            issuer                agentgateway            mock-llm
(no secrets)     (signs identity)      (holds credentials)     (checks them)
    |                 |                      |                     |
    |-- POST /token ->|                      |                     |
    |<- 60s JWT ------|                      |                     |
    |                                        |                     |
    |-- Bearer <60s JWT> ------------------->|                     |
    |                            jwtAuth: strict, JWKS from issuer |
    |                            authorization: CEL on jwt.team    |
    |                                        |                     |
    |                                        |-- provider key ---->|  /static
    |                                        |-- minted JWT ------>|  /signed
    |<- 200 -------------------------------- |                     |
```

The upstream returns 401 when the credential is missing or wrong. That is
what makes this a demonstration rather than an assertion: if a response comes
back at all, the gateway must have attached something the agent never had.

Four services, one image for the Python ones:

| Service | Holds | Notes |
|---|---|---|
| `agent` | nothing | the point of the demo |
| `issuer` | a signing key for identity tokens | no provider credential |
| `agentgateway` | the provider key, the upstream signing key | v1.5.0, standalone mode |
| `mock-llm` | the provider key | it is the provider |

## Quickstart

Requirements: Docker with Compose v2. No provider account, no API key.

```bash
git clone https://github.com/themsquared/secretless-agents.git
cd secretless-agents
docker compose up -d --build
```

Then run the assertions:

```bash
./scripts/verify.sh
```

Or walk through it with narration:

```bash
./scripts/demo.sh
```

Tear down:

```bash
docker compose down -v && rm -rf keys
```

A clean run from an empty checkout (`up -d --build`, then `verify.sh`) reports
`14 passed, 0 failed`. Every command in this README was run against
agentgateway v1.5.0 on Docker Desktop for macOS (arm64).

## What the agent holds

```bash
docker compose exec agent sh -c 'env | grep -iE "api_key|secret|token|password" || echo NONE'
```

```
NONE
```

The agent's own source (`python-tools/agent.py`) is worth reading for the
same reason: there is no credential in it to find.

## How agentgateway enforces identity

The inbound half is one policy block. `mode: strict` means a request without
a valid token from this issuer never reaches a model:

```yaml
llm:
  policies:
    jwtAuth:
      mode: strict
      issuer: "https://issuer.secretless.local"
      audiences: ["agentgateway"]
      jwks:
        file: /keys/jwks.json
```

Identity is not authorization, so the claims get their own rules. A valid
token gets an agent to the gateway and no further:

```yaml
    authorization:
      rules:
      - allow: 'has(jwt.team)'
```

What each team may actually call is decided per model:

```yaml
  models:
  - name: secretless-static
    authorization:
      rules:
      - allow: 'jwt.team in ["research", "platform"]'
  - name: secretless-signed
    authorization:
      rules:
      - allow: 'jwt.team == "platform"'
```

A research agent asking for the model it is not cleared for gets a 403 with a
perfectly valid token:

```bash
docker compose exec agent python3 /app/agent.py --agent research --model secretless-signed
```

```
HTTP 403
{
  "error": {
    "message": "Model authorization denied",
    "type": "invalid_request_error",
    "code": "model_authorization_denied"
  }
}
```

That distinction matters: a stolen token is still bounded by what its team was
allowed to do.

## How the provider credential gets attached

Two providers, two upstream auth styles. The first is the familiar one, with
the key moved off the agent and onto the gateway:

```yaml
  - name: static-key
    params:
      baseUrl: http://mock-llm:8088/static
    defaults:
      auth:
        key: $UPSTREAM_API_KEY
```

The second is the one worth building toward. `jwtSign` has agentgateway sign
a fresh JWT with its own private key on every single request:

```yaml
  - name: signed-jwt
    params:
      baseUrl: http://mock-llm:8088/signed
    defaults:
      auth:
        jwtSign:
          signingKey:
            file: /keys/gateway-sign.key
          alg: RS256
          ttl: 60s
          claims:
            iss: agentgateway
            aud: mock-llm
            sub: agentgateway/llm-egress
```

There is no long-lived bearer token on that connection to capture. The
upstream verifies the signature and reports how long what it received has
left, so you can see it rather than take it on faith:

```bash
docker compose exec agent python3 /app/agent.py --agent platform --model secretless-signed
```

```
HTTP 200
authenticated with a JWT agentgateway minted for this request, sub=agentgateway/llm-egress expires_in=60s
```

## The breach scenario

The honest test of the pattern is: assume the agent is fully compromised and
the attacker takes everything it has. What is that worth?

```bash
docker compose exec agent python3 /app/agent.py --agent platform --direct
```

```
HTTP 401
{
  "error": {
    "message": "upstream rejected the request: wrong or missing provider key",
    "type": "invalid_request_error"
  }
}
```

The agent's token goes straight to the provider, bypassing the gateway. The
provider rejects it, because an identity assertion is not a provider
credential. Sixty seconds later it is not even that.

The reverse also holds. The provider key, if someone did get it, is not a
gateway credential either:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:3300/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-upstream-do-not-leak' \
  -d '{"model":"secretless-static","messages":[{"role":"user","content":"hi"}]}'
```

```
401
```

## Gotchas

**`error looking key 'UPSTREAM_API_KEY' up: environment variable not found`**

agentgateway expands `$VAR` in the config at load time and fails closed when
the variable is unset. That is the desired behavior, but it means
`--validate-only` needs the same environment the real run has:

```bash
docker run --rm -e UPSTREAM_API_KEY=dummy \
  -v "$PWD/config:/config:ro" -v "$PWD/keys:/keys:ro" \
  cr.agentgateway.dev/agentgateway:v1.5.0 -f /config/agentgateway.yaml --validate-only
```

**`failed to load JWKS: read resource file /keys/jwks.json`**

The gateway resolves the JWKS when it loads the config, not lazily on the
first request. The keys have to exist before it starts. That is why `keygen`
is a one-shot service other services wait on with
`condition: service_completed_successfully` rather than a step in a script.

`jwks` also accepts `{url: ...}` for a remote JWKS endpoint, which is what a
real deployment uses. This demo mounts a file so `docker compose up` is
deterministic and does not race the issuer's readiness.

**An expired token is still accepted for 60 more seconds**

Not a bug. agentgateway allows 60 seconds of clock skew on `exp`, so a token
that expired 30 seconds ago validates and one that expired 61 seconds ago does
not. `verify.sh` asserts both, because the number matters when you are
choosing a token lifetime:

```bash
docker compose exec agent python3 /app/agent.py --ttl -30    # HTTP 200
docker compose exec agent python3 /app/agent.py --ttl -120   # HTTP 401
```

```
authentication failure: the token is invalid or malformed: Error(ExpiredSignature)
```

**`upstream call failed: Connect: Connection refused (os error 111)`**

The gateway resolved the upstream's address once and kept it. Recreating just
the `mock-llm` container gives it a new IP and every request 503s until the
gateway is restarted:

```bash
docker compose restart agentgateway
```

Worth knowing before you blame the config. In Kubernetes a Service address
hides this; with raw compose DNS it shows up immediately.

**The minted upstream token's `exp - iat` is 70 seconds, not 60**

`ttl: 60s` sets `exp` to 60 seconds from now, and agentgateway backdates `iat`
by 10 seconds for skew. The token is valid for 60 seconds from when it was
minted, which is what the upstream reports as `expires_in`.

## What this is not

- Not a benchmark. The upstream is a mock and returns fixed usage numbers.
- Not a complete authorization model. Two CEL rules on one claim is the
  smallest thing that shows identity and authorization are separate.
- Not multi-tenant key management. agentgateway v1.5.0 also does per-key
  budgets and model allowlists, which is a different demo.

## Topics

`agentgateway` `mcp` `ai-agents` `kubernetes` `ai-gateway` `jwt` `spiffe`
`zero-trust` `llm` `platform-engineering`

## License

Apache-2.0.
