import contextlib
import os
from collections.abc import AsyncIterator

import uvicorn
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.responses import Response

BALANCES = {
    "user123": 1500.00,
    "user456": 250.50,
}

server = Server("FinanceServer")


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_account_balance",
            description="Get the current balance for a user. (Read-only)",
            inputSchema={
                "type": "object",
                "properties": {"user_id": {"type": "string"}},
                "required": ["user_id"],
            },
            annotations={"readOnlyHint": True},
        ),
        Tool(
            name="transfer_funds",
            description="Transfer funds between accounts. (Write operation)",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_user": {"type": "string"},
                    "to_user": {"type": "string"},
                    "amount": {"type": "number"},
                },
                "required": ["from_user", "to_user", "amount"],
            },
            annotations={"readOnlyHint": False},
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "get_account_balance":
        user_id = arguments.get("user_id")
        balance = BALANCES.get(user_id, 0.0)
        return [TextContent(type="text", text=f"Balance for {user_id}: ${balance:,.2f}")]

    elif name == "transfer_funds":
        from_user = arguments.get("from_user")
        to_user = arguments.get("to_user")
        amount = arguments.get("amount")

        if from_user not in BALANCES:
            return [TextContent(type="text", text=f"Error: Source user {from_user} not found.")]

        if BALANCES[from_user] < amount:
            return [TextContent(type="text", text=f"Error: Insufficient funds in {from_user}'s account.")]

        BALANCES[from_user] -= amount
        if to_user in BALANCES:
            BALANCES[to_user] += amount

        return [TextContent(type="text", text=f"Successfully transferred ${amount:,.2f} from {from_user} to {to_user}.")]

    raise ValueError(f"Tool not found: {name}")


session_manager = StreamableHTTPSessionManager(app=server, stateless=True)


@contextlib.asynccontextmanager
async def lifespan(_app: Starlette) -> AsyncIterator[None]:
    async with session_manager.run():
        yield


async def routing_app(scope, receive, send):
    if scope["type"] != "http":
        return
    if scope["path"] == "/mcp":
        await session_manager.handle_request(scope, receive, send)
    else:
        response = Response("Not Found", status_code=404)
        await response(scope, receive, send)


app = Starlette(lifespan=lifespan)
app.mount("/", app=routing_app)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
