"""Keycloak / OIDC configuration loaded from env."""

import os


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required env var {name} is not set")
    return value


KEYCLOAK_URL = _required("KEYCLOAK_URL").rstrip("/")
KEYCLOAK_REALM = _required("KEYCLOAK_REALM")
KEYCLOAK_AUDIENCE = os.environ.get("KEYCLOAK_AUDIENCE", "account")
VERIFY_AUDIENCE = (
    os.environ.get("KEYCLOAK_VERIFY_AUDIENCE", "true").lower() == "true"
)

ISSUER = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"

PORT = int(os.environ.get("PORT", "8080"))
