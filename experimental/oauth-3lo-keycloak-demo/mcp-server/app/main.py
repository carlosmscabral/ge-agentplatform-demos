"""FastMCP server entrypoint — Keycloak-protected MCP for the 3LO demo.

FastMCP 2.x exposes its Starlette ASGI app via `mcp.http_app(path=...)`.
We pass our KeycloakAuthMiddleware via the `middleware` arg (wrapped in
Starlette's Middleware helper) so every request to /mcp is authenticated
before it reaches the MCP session manager. Cloud Run sets $PORT.
"""

import logging

import uvicorn
from fastmcp import FastMCP
from starlette.middleware import Middleware

from app.common import register_all
from app.config import PORT
from app.middleware import KeycloakAuthMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("oauth-3lo-mcp")

mcp = FastMCP("oauth-3lo-keycloak-mcp")
register_all(mcp)

app = mcp.http_app(
    path="/mcp",
    middleware=[Middleware(KeycloakAuthMiddleware)],
    stateless_http=False,
)


def main() -> None:
    logger.info("Starting oauth-3lo-mcp on 0.0.0.0:%d (path=/mcp)", PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
