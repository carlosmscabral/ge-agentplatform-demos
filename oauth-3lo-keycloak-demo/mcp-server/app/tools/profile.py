"""MCP tools that expose the authenticated user's identity context.

We read claims from `request.state.claims` via FastMCP's `get_http_request()`
helper. ContextVar-based access (was our original approach) does NOT survive
FastMCP's task boundaries — the tool runs in a different asyncio context
from the Starlette middleware that set the ContextVar, so it'd come back
as None inside the tool. The Starlette request object, on the other hand,
is request-scoped and accessible from anywhere in the request lifecycle
via FastMCP's helper.
"""

from fastmcp.server.dependencies import get_http_request


def _claims() -> dict:
    request = get_http_request()
    claims = getattr(request.state, "claims", None)
    if claims is None:
        # Middleware enforces auth on /mcp routes for tools/call — this path
        # should never fire — but guard for robustness.
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
