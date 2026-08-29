"""Generate the two keypairs the demo needs, plus a JWKS for the issuer key.

Runs once, before anything else, and writes into the shared ./keys volume:

  issuer.key        RSA private key. The token issuer signs agent identity
                    tokens with it. Stands in for whatever mints workload
                    identity in a real cluster (SPIFFE, an OIDC provider,
                    a cloud IAM token service).
  jwks.json         Public half of issuer.key in JWKS form. agentgateway
                    validates incoming agent tokens against this.
  gateway-sign.key  RSA private key that agentgateway uses to mint a fresh
                    upstream JWT on every request (backendAuth jwtSign).
  gateway-sign.pub  Public half. The upstream verifies with it.

Nothing here is a long-lived provider credential, which is the point.
"""

import base64
import json
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

KEYS = "/keys"


def b64url(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def write_private(key, path):
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(path, "wb") as f:
        f.write(pem)
    os.chmod(path, 0o600)


def write_public(key, path):
    pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with open(path, "wb") as f:
        f.write(pem)


def main():
    if os.path.exists(f"{KEYS}/jwks.json"):
        print("keygen: keys already present, nothing to do")
        return

    issuer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    write_private(issuer, f"{KEYS}/issuer.key")

    numbers = issuer.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": "issuer-1",
                "n": b64url(numbers.n),
                "e": b64url(numbers.e),
            }
        ]
    }
    with open(f"{KEYS}/jwks.json", "w") as f:
        json.dump(jwks, f, indent=2)

    gw = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    write_private(gw, f"{KEYS}/gateway-sign.key")
    write_public(gw, f"{KEYS}/gateway-sign.pub")

    print("keygen: wrote issuer.key, jwks.json, gateway-sign.key, gateway-sign.pub")


if __name__ == "__main__":
    main()
