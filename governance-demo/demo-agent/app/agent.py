import os

from google.adk.agents import Agent

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL")
MCP_SERVER_NAME = os.environ.get("MCP_SERVER_NAME")
REGION = os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")


def _build_mcp_toolset():
    if MCP_SERVER_NAME:
        from google.adk.integrations.agent_registry import AgentRegistry
        from google.auth import default

        _, project_id = default()
        registry = AgentRegistry(project_id=project_id, location=REGION)
        return registry.get_mcp_toolset(MCP_SERVER_NAME)

    from google.adk.tools.mcp_tool import McpToolset
    from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams

    url = MCP_SERVER_URL or "http://localhost:8080/sse"
    return McpToolset(connection_params=SseConnectionParams(url=url))


mcp_toolset = _build_mcp_toolset()

root_agent = Agent(
    name="governance_demo_agent",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    instruction=(
        "You are a financial assistant. You have access to a user's mock financial tools "
        "via an MCP server. You can check balances and transfer funds. "
        "Always be polite and helpful. If a transaction is blocked, inform the user "
        "that it might be due to security policies."
    ),
    tools=[mcp_toolset],
)

app = root_agent
