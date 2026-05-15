import logging
import os

import google.auth
import google.auth.transport.requests
import httpx
from google.adk.agents import Agent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.apps import App

logger = logging.getLogger(__name__)

_, project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id or "")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

SPECIALIST_REGISTRY_NAME = os.environ.get("SPECIALIST_REGISTRY_NAME", "")
SPECIALIST_A2A_CARD_URL = os.environ.get("SPECIALIST_A2A_CARD_URL", "")
REGISTRY_LOCATION = os.environ.get("REGISTRY_LOCATION", "us-central1")


class _GCPAuth(httpx.Auth):
    """httpx auth handler that refreshes GCP credentials on each request."""

    def __init__(self):
        self._credentials, _ = google.auth.default()

    def auth_flow(self, request):
        self._credentials.refresh(google.auth.transport.requests.Request())
        request.headers["Authorization"] = f"Bearer {self._credentials.token}"
        yield request


_auth_httpx_client = httpx.AsyncClient(
    auth=_GCPAuth(),
    timeout=httpx.Timeout(timeout=120),
)


def _discover_specialist() -> RemoteA2aAgent | None:
    """Discover the currency specialist via Agent Registry or fallback to URL."""
    if SPECIALIST_REGISTRY_NAME:
        try:
            from google.adk.integrations.agent_registry import AgentRegistry

            registry = AgentRegistry(
                project_id=project_id,
                location=REGISTRY_LOCATION,
            )
            agent = registry.get_remote_a2a_agent(
                agent_name=SPECIALIST_REGISTRY_NAME,
                httpx_client=_auth_httpx_client,
            )
            logger.info(
                "Discovered specialist via Agent Registry: %s",
                SPECIALIST_REGISTRY_NAME,
            )
            return agent
        except Exception:
            logger.warning(
                "Registry discovery failed for %s, trying URL fallback",
                SPECIALIST_REGISTRY_NAME,
                exc_info=True,
            )

    if SPECIALIST_A2A_CARD_URL:
        logger.info("Using fallback URL: %s", SPECIALIST_A2A_CARD_URL)
        return RemoteA2aAgent(
            name="spiffe_currency_specialist",
            description="Agente especialista em câmbio: consulta taxas e converte valores entre moedas.",
            agent_card=SPECIALIST_A2A_CARD_URL,
            use_legacy=False,
            httpx_client=_auth_httpx_client,
        )

    logger.warning("No specialist configured — orchestrator will answer directly")
    return None


specialist = _discover_specialist()
sub_agents = [specialist] if specialist else []

root_agent = Agent(
    name="spiffe_orchestrator_agent",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    instruction=(
        "Você é um agente orquestrador. Responda sempre em português brasileiro.\n\n"
        "Você coordena tarefas delegando para agentes especialistas:\n"
        "- Para questões de câmbio, conversão de moedas ou taxas de câmbio, "
        "delegue para o spiffe_currency_specialist.\n"
        "- Para outras perguntas, responda diretamente com seu conhecimento geral.\n\n"
        "Ao delegar, repasse a pergunta completa do usuário para o especialista.\n"
        "Quando receber a resposta do especialista, repasse ao usuário de forma clara."
    ),
    sub_agents=sub_agents,
)

app = App(root_agent=root_agent, name="app")
