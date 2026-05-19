"""MCP tools that expose the authenticated user's identity context."""

from app.middleware import current_claims


def _claims() -> dict:
    claims = current_claims.get()
    if claims is None:
        # Middleware enforces auth on /mcp routes, so this path should never
        # fire — but guard anyway for robustness.
        raise RuntimeError("no authenticated user context")
    return claims


def get_my_profile() -> dict:
    """Return the calling user's validated Keycloak claims."""
    c = _claims()
    return {
        "sub": c.get("sub"),
        "username": c.get("preferred_username"),
        "email": c.get("email"),
        "email_verified": c.get("email_verified"),
        "given_name": c.get("given_name"),
        "family_name": c.get("family_name"),
        "realm_roles": c.get("realm_access", {}).get("roles", []),
        "issued_at": c.get("iat"),
        "expires_at": c.get("exp"),
    }


def echo(message: str) -> dict:
    """Echo a message back tagged with the authenticated subject."""
    c = _claims()
    return {
        "message": message,
        "echoed_by_sub": c.get("sub"),
        "echoed_by_username": c.get("preferred_username"),
    }
