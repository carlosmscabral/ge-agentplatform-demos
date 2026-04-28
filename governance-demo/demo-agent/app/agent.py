import os

from google.adk.agents import Agent

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL")
USE_AGENT_REGISTRY = os.environ.get("USE_AGENT_REGISTRY", "").lower() == "true"
PROJECT_ID = os.environ.get("PROJECT_ID")
REGION = os.environ.get("REGION", "us-central1")
AGENT_REGISTRY_SERVICE_NAME = os.environ.get(
    "AGENT_REGISTRY_SERVICE_NAME", "finance-mcp-service"
)


def _build_mcp_toolset():
    if USE_AGENT_REGISTRY and PROJECT_ID:
        from google.adk.integrations.agent_registry import AgentRegistry

        registry = AgentRegistry(project_id=PROJECT_ID, location=REGION)
        return registry.get_mcp_toolset(
            mcp_server_name=f"mcpServers/{AGENT_REGISTRY_SERVICE_NAME}"
        )

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
