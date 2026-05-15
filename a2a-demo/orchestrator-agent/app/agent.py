import os

import google.auth
import google.auth.transport.requests
import httpx
from google.adk.agents import Agent
from google.adk.apps import App

_, project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id or "")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

SPECIALIST_A2A_CARD_URL = os.environ.get("SPECIALIST_A2A_CARD_URL", "")


class _GCPAuth(httpx.Auth):
    """httpx auth handler that refreshes GCP credentials on each request."""

    def __init__(self):
        self._credentials, _ = google.auth.default()

    def auth_flow(self, request):
        self._credentials.refresh(google.auth.transport.requests.Request())
        request.headers["Authorization"] = f"Bearer {self._credentials.token}"
        yield request


sub_agents = []

if SPECIALIST_A2A_CARD_URL:
    from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

    auth_httpx_client = None
    if "aiplatform.googleapis.com" in SPECIALIST_A2A_CARD_URL:
        auth_httpx_client = httpx.AsyncClient(
            auth=_GCPAuth(),
            timeout=httpx.Timeout(timeout=120),
        )

    currency_specialist = RemoteA2aAgent(
        name="currency_specialist",
        description="Agente especialista em câmbio: consulta taxas e converte valores entre moedas.",
        agent_card=SPECIALIST_A2A_CARD_URL,
        use_legacy=False,
        httpx_client=auth_httpx_client,
    )
    sub_agents = [currency_specialist]

root_agent = Agent(
    name="orchestrator_agent",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    instruction=(
        "Você é um agente orquestrador. Responda sempre em português brasileiro.\n\n"
        "Você coordena tarefas delegando para agentes especialistas:\n"
        "- Para questões de câmbio, conversão de moedas ou taxas de câmbio, "
        "delegue para o currency_specialist.\n"
        "- Para outras perguntas, responda diretamente com seu conhecimento geral.\n\n"
        "Ao delegar, repasse a pergunta completa do usuário para o especialista.\n"
        "Quando receber a resposta do especialista, repasse ao usuário de forma clara."
    ),
    sub_agents=sub_agents,
)

app = App(root_agent=root_agent, name="app")
