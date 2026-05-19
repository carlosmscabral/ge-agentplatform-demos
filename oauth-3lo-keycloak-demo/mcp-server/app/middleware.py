"""Starlette middleware that validates Keycloak JWTs on every MCP request.

Validated claims are stashed on `request.state.claims` AND in a ContextVar
so that FastMCP tools can read them via either path (FastMCP's Context API
exposes the Starlette request, but the ContextVar is a safe fallback in
case the version pinned here doesn't surface it cleanly).
"""

"""ASGI middleware: validates Keycloak JWT on every /mcp request, EXCEPT for
discovery methods (`initialize`, `tools/list`, etc) which are public.

We use a raw ASGI middleware (not Starlette's BaseHTTPMiddleware) because
BaseHTTPMiddleware breaks SSE streaming when we peek at the request body
(the message-replay trick is incompatible with the SSE response protocol
that FastMCP uses on /mcp).
"""

import contextvars
import json
import logging
from typing import Optional

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth import TokenValidationError, verify_keycloak_jwt

logger = logging.getLogger(__name__)

# ContextVar mirrors request.state.claims so tools can read it regardless of
# how FastMCP wires its Context object.
current_claims: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "current_claims", default=None
)

# Paths that don't require any auth check (liveness, root probe).
_PUBLIC_PATHS = {"/health", "/"}

# JSON-RPC methods the MCP client calls at startup/discovery. None of these
# return user-specific data — only protocol info and tool schemas (which are
# also public via the Agent Registry's toolspec). Allowing them without a
# Bearer lets ADK build the toolset before any user has consented.
_PUBLIC_RPC_METHODS = {
    "initialize",
    "notifications/initialized",
    "tools/list",
    "prompts/list",
    "resources/list",
    "resources/templates/list",
}


async def _send_json_error(send: Send, status: int, body: dict, extra_headers: list[tuple[bytes, bytes]] = ()) -> None:
    body_bytes = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body_bytes)).encode())]
    headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body_bytes, "more_body": False})


class KeycloakAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        method = scope.get("method", "")

        if path in _PUBLIC_PATHS:
            return await self.app(scope, receive, send)

        # MCP Streamable HTTP transport semantics:
        #   - GET  /mcp  → opens the server→client SSE notification stream,
        #                  carries session state (Mcp-Session-Id), not user data.
        #                  Allow unauthenticated — analogous to opening a websocket.
        #   - DELETE /mcp → terminate session. No user data. Allow.
        #   - POST /mcp  → send a JSON-RPC message. Peek at body, require Bearer
        #                  ONLY for methods that touch user data (tools/call,
        #                  prompts/get, resources/read).
        # Buffer the request body so we can peek AND pass it through downstream.
        messages: list[Message] = []
        if method == "POST":
            more = True
            while more:
                msg = await receive()
                messages.append(msg)
                more = msg.get("more_body", False)
            body = b"".join(m.get("body", b"") for m in messages)
        else:
            body = b""

        # Decide auth requirement based on HTTP method + JSON-RPC method.
        is_public_rpc = method in ("GET", "DELETE")
        if method == "POST" and body:
            try:
                payload = json.loads(body)
                rpc_method = payload.get("method", "") if isinstance(payload, dict) else ""
                if rpc_method in _PUBLIC_RPC_METHODS:
                    is_public_rpc = True
                    logger.info(
                        "Bypassing auth for public MCP method '%s' (path=%s)",
                        rpc_method, path,
                    )
            except (json.JSONDecodeError, ValueError):
                pass  # not JSON; auth still required

        if not is_public_rpc:
            # Validate Bearer.
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode()
            if not auth_header.startswith("Bearer "):
                logger.info("Rejecting %s %s: missing Authorization", method, path)
                return await _send_json_error(
                    send, 401, {"error": "missing_bearer"},
                    extra_headers=[(b"www-authenticate", b'Bearer realm="oauth-3lo-mcp"')],
                )
            token = auth_header[len("Bearer "):].strip()
            try:
                claims = verify_keycloak_jwt(token)
            except TokenValidationError as e:
                logger.warning("Rejecting %s %s: %s", method, path, e)
                return await _send_json_error(send, 403, {"error": "invalid_token", "detail": str(e)})

            token_var = current_claims.set(claims)
            logger.info(
                "Authenticated %s %s as sub=%s username=%s",
                method, path, claims.get("sub"), claims.get("preferred_username"),
            )
        else:
            token_var = None

        # Replay the buffered body to the downstream app.
        idx = [0]
        async def replay_receive() -> Message:
            if idx[0] < len(messages):
                msg = messages[idx[0]]
                idx[0] += 1
                return msg
            # After all buffered messages are consumed, fall back to the
            # original receive (for disconnect signals etc).
            return await receive()

        try:
            await self.app(scope, replay_receive, send)
        finally:
            if token_var is not None:
                current_claims.reset(token_var)
