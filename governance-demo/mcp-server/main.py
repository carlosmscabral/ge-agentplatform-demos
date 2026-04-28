import os
import uvicorn
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, ListToolsResult, TextContent, CallToolResult
from starlette.applications import Starlette
from starlette.routing import Route

# Mock data
BALANCES = {
    "user123": 1500.00,
    "user456": 250.50
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
                "properties": {
                    "user_id": {"type": "string"}
                },
                "required": ["user_id"]
            },
            annotations={
                "readOnlyHint": True
            }
        ),
        Tool(
            name="transfer_funds",
            description="Transfer funds between accounts. (Write operation)",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_user": {"type": "string"},
                    "to_user": {"type": "string"},
                    "amount": {"type": "number"}
                },
                "required": ["from_user", "to_user", "amount"]
            },
            annotations={
                "readOnlyHint": False
            }
        )
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

sse = SseServerTransport("/messages")

async def handle_sse(request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="FinanceServer",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

async def handle_messages(request):
    await sse.handle_post_message(request.scope, request.receive, request._send)

app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse),
        Route("/messages", endpoint=handle_messages, methods=["POST"]),
    ]
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
