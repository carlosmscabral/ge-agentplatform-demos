import os
from google.adk.agents import Agent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams

# In production, this will be the Cloud Run URL
mcp_server_url = os.environ.get("MCP_SERVER_URL", "http://localhost:8080/sse")

# Define the MCP Toolset
mcp_toolset = McpToolset(
    connection_params=SseConnectionParams(
        url=mcp_server_url
    )
)

# Initialize the Agent
root_agent = Agent(
    name="governance_demo_agent",
    model="gemini-3-flash-preview",
    instruction=(
        "You are a financial assistant. You have access to a user's mock financial tools "
        "via an MCP server. You can check balances and transfer funds. "
        "Always be polite and helpful. If a transaction is blocked, inform the user "
        "that it might be due to security policies."
    ),
    tools=[mcp_toolset],
)

# Export as 'app' for the template
app = root_agent
