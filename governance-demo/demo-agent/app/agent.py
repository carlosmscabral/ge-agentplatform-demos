import os

from google.adk.agents import Agent
from google.adk.tools.base_toolset import BaseToolset

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
    from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

    url = MCP_SERVER_URL or "http://localhost:8080/mcp"
    return McpToolset(connection_params=StreamableHTTPConnectionParams(url=url))


class _LazyToolset(BaseToolset):
    """Defers MCP toolset construction until first use."""

    def __init__(self):
        super().__init__()
        self._inner = None

    def _resolve(self):
        if self._inner is None:
            self._inner = _build_mcp_toolset()
        return self._inner

    async def get_tools(self, readonly_context=None):
        return await self._resolve().get_tools(readonly_context)

    async def close(self):
        if self._inner is not None:
            await self._inner.close()


BaseToolset.register(_LazyToolset)

mcp_toolset = _LazyToolset()

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
