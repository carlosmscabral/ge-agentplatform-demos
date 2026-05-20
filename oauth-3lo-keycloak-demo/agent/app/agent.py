"""ADK agent that calls a Keycloak-protected MCP server on behalf of the user.

The agent itself runs on Agent Runtime with SPIFFE identity
(IdentityType.AGENT_IDENTITY) and outbound MCP calls are authenticated using
Agent Identity 3-Legged OAuth.

This module does NOT know anything about the OAuth provider — the
auth provider, continue_uri, and scopes live in an **Agent Registry
Binding** ((agent, MCP server, auth_provider) triple, created by deploy.sh).
At runtime, `AgentRegistry.get_mcp_toolset(...)` resolves the binding,
pulls the user's Keycloak token from the Google-managed credential vault,
and injects it as `Authorization: Bearer <user_token>` on every MCP request.

The MCP toolset is wrapped in `_LazyToolset` so binding resolution is
deferred until the first tool call. Reason: `agents-cli deploy` creates the
agent BEFORE deploy.sh creates the binding (chicken-and-egg — binding needs
agent URN). If the agent eagerly resolved the binding at module load, it
would find none and silently fall back to no-auth — every later tool call
would 401. Lazy resolution sidesteps the race: by the time the first user
request arrives, the binding exists.

Agent code stays free of auth wiring: swapping IdPs, scopes, or callback
URLs is a deploy-time concern handled in a single `bindings update` call.
"""

import logging
import os
from typing import Any

import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.auth.credential_manager import CredentialManager
from google.adk.integrations.agent_identity import GcpAuthProvider
from google.adk.integrations.agent_registry import AgentRegistry
from google.adk.tools.base_toolset import BaseToolset

logger = logging.getLogger(__name__)

_, _project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", _project_id or "")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT"]
MCP_REGISTRY_NAME = os.environ.get("MCP_REGISTRY_NAME", "")
REGISTRY_LOCATION = os.environ.get("REGISTRY_LOCATION", "global")
CONTINUE_URI = os.environ.get("CONTINUE_URI", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")


class _LazyMcpToolset(BaseToolset):
    """Wraps `registry.get_mcp_toolset(...)` and defers materialization.

    Same pattern used by mcp-discovery-demo. Materialization is deferred to
    the first `get_tools()` call so:

      1. Module import succeeds even before the Agent Registry Binding exists
         (deploy.sh creates the agent FIRST, then the binding — agent code
         loaded at boot wouldn't find the binding).
      2. Healthcheck and `agents-cli deploy` introspection don't need the
         Registry to be reachable.
      3. Transient Registry/network glitches during agent boot don't poison
         the toolset for the lifetime of the agent.
    """

    def __init__(self, mcp_server_name: str, continue_uri: str) -> None:
        super().__init__()
        self._mcp_server_name = mcp_server_name
        self._continue_uri = continue_uri
        self._inner: Any = None

    def _resolve(self) -> Any:
        if self._inner is None:
            logger.info(
                "Materializing MCP toolset from registry %s "
                "(auth provider resolved via Binding, continue_uri=%s)",
                self._mcp_server_name, self._continue_uri,
            )
            # Registering the provider lets CredentialManager resolve the
            # GcpAuthProviderScheme that get_mcp_toolset constructs from
            # the Binding's authProviderBinding field.
            CredentialManager.register_auth_provider(GcpAuthProvider())
            registry = AgentRegistry(
                project_id=PROJECT_ID, location=REGISTRY_LOCATION
            )
            self._inner = registry.get_mcp_toolset(
                mcp_server_name=self._mcp_server_name,
                continue_uri=self._continue_uri,
            )
        return self._inner

    async def get_tools(self, readonly_context=None):
        return await self._resolve().get_tools(readonly_context)

    async def close(self):
        if self._inner is not None:
            await self._inner.close()


BaseToolset.register(_LazyMcpToolset)


def _build_tools() -> list:
    """Return the (lazy) MCP toolset. Empty list if env vars missing.

    During local introspection (e.g., `agents-cli deploy` running the agent
    module to extract metadata), MCP_REGISTRY_NAME is empty. Return an empty
    tool list so the import succeeds — the deployed Agent Runtime has the
    real value via env vars and the LazyToolset materializes on first use.
    """
    if not MCP_REGISTRY_NAME:
        logger.warning(
            "MCP_REGISTRY_NAME missing. Agent loads with NO tools — fine "
            "for introspection, broken at runtime."
        )
        return []
    return [_LazyMcpToolset(MCP_REGISTRY_NAME, CONTINUE_URI)]


root_agent = Agent(
    name="oauth_3lo_keycloak_agent",
    model=GEMINI_MODEL,
    instruction=(
        "Você é um agente de demonstração que acessa um servidor MCP "
        "protegido por Keycloak em nome do usuário final, usando OAuth 3LO "
        "via Agent Identity Connector.\n\n"
        "FERRAMENTAS DISPONÍVEIS (use EXATAMENTE esses nomes — não invente, "
        "não traduza, não altere case):\n"
        "  - get_my_profile         — sem argumentos. Retorna sub, username, "
        "email, realm_roles do JWT validado.\n"
        "  - echo                   — argumentos: { message: string }. Eco "
        "tagueado com o sub do usuário autenticado.\n\n"
        "Quando o usuário perguntar sobre o próprio perfil/identidade, "
        "chame `get_my_profile` (sem argumentos).\n\n"
        "Se o consentimento ainda não tiver sido concedido, você receberá "
        "uma chamada de função `adk_request_credential` — repasse-a ao "
        "cliente sem modificar. Após o consentimento, **chame a MESMA "
        "ferramenta de novo com EXATAMENTE o mesmo nome** — o token será "
        "injetado automaticamente.\n\n"
        "Responda sempre em português brasileiro, de forma clara, e "
        "explique brevemente o que cada ferramenta retornou."
    ),
    tools=_build_tools(),
)

app = App(root_agent=root_agent, name="app")
