"""Keycloak JWT validation via JWKS.

Uses PyJWT with PyJWKClient — keys are fetched from
`<keycloak>/realms/<realm>/protocol/openid-connect/certs` and cached for
1 hour; the client auto-refreshes on unknown kid (Keycloak key rotation).
"""

import jwt
from jwt import PyJWKClient

from app.config import (
    ISSUER,
    JWKS_URL,
    KEYCLOAK_AUDIENCE,
    VERIFY_AUDIENCE,
)

_jwks_client = PyJWKClient(JWKS_URL, cache_keys=True, max_cached_keys=10)


class TokenValidationError(Exception):
    """Raised when the bearer token is missing, malformed, or invalid."""


def verify_keycloak_jwt(token: str) -> dict:
    """Validate a Keycloak-issued JWT and return its decoded claims.

    Raises TokenValidationError on any failure (expired, bad signature,
    wrong issuer, audience mismatch, unknown kid, etc.).
    """
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=ISSUER,
            audience=KEYCLOAK_AUDIENCE if VERIFY_AUDIENCE else None,
            options={"verify_aud": VERIFY_AUDIENCE},
        )
    except jwt.ExpiredSignatureError as e:
        raise TokenValidationError("token_expired") from e
    except jwt.InvalidAudienceError as e:
        raise TokenValidationError(
            f"audience_mismatch (expected '{KEYCLOAK_AUDIENCE}')"
        ) from e
    except jwt.InvalidIssuerError as e:
        raise TokenValidationError(
            f"issuer_mismatch (expected '{ISSUER}')"
        ) from e
    except jwt.InvalidSignatureError as e:
        raise TokenValidationError("invalid_signature") from e
    except Exception as e:
        raise TokenValidationError(f"invalid_token: {e}") from e
