"""fintoolkit orchestrator — Financial analyst with dynamic MCP discovery.

Architecture:
  * Three MCP servers (market-data, portfolio, news-sentiment) run on Cloud Run.
  * deploy.sh registers each in Agent Registry with `tag=...` attributes and
    injects three env vars (MARKET_MCP_URL / PORTFOLIO_MCP_URL / NEWS_MCP_URL).
  * This module instantiates THREE _LazyToolsets (one per MCP server), each
    deferring `McpToolset` construction until first use — registry / network
    are not available during Agent Runtime health checks (see LEARNINGS.md L100).
  * Two meta-tools (discover_tools_by_intent, discover_tools_by_category) call
    the Agent Registry to let the LLM introspect what's available.
"""

from __future__ import annotations

import logging
import os

import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from app.discovery import discover_tools_by_category, discover_tools_by_intent
from app.mcp_auth import make_cr_header_provider

logger = logging.getLogger(__name__)

_, _project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", _project_id or "")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")


def _audience_from_mcp_url(url: str) -> str:
    """ID-token audience = service base URL (no trailing path)."""
    # Strip /mcp or any path component — Cloud Run validates aud as exact base URL.
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}"


def _build_toolset_for(url_env_var: str, prefix: str) -> McpToolset:
    """Construct an McpToolset pointing at the given Cloud Run / local MCP URL."""
    url = os.environ.get(url_env_var, "")
    if not url:
        raise RuntimeError(f"env var {url_env_var} is empty — cannot build toolset")
    is_local = url.startswith("http://localhost") or url.startswith("http://127.")
    kwargs: dict = {
        "connection_params": StreamableHTTPConnectionParams(url=url),
        "tool_name_prefix": prefix,
    }
    if not is_local:
        kwargs["header_provider"] = make_cr_header_provider(_audience_from_mcp_url(url))
    return McpToolset(**kwargs)


class _LazyToolset(BaseToolset):
    """Defers MCP toolset construction until first use (LEARNINGS.md L100).

    Agent Runtime imports this module during health checks. At import time the
    registry / Cloud Run services may not be reachable yet, so we hold off until
    the first `get_tools()` call.
    """

    def __init__(self, url_env_var: str, prefix: str):
        super().__init__()
        self._url_env_var = url_env_var
        self._prefix = prefix
        self._inner: McpToolset | None = None

    def _resolve(self) -> McpToolset:
        if self._inner is None:
            logger.info("Materializing MCP toolset: prefix=%s env=%s", self._prefix, self._url_env_var)
            self._inner = _build_toolset_for(self._url_env_var, self._prefix)
        return self._inner

    async def get_tools(self, readonly_context=None):
        return await self._resolve().get_tools(readonly_context)

    async def close(self):
        if self._inner is not None:
            await self._inner.close()


BaseToolset.register(_LazyToolset)


market_toolset = _LazyToolset("MARKET_MCP_URL", "market")
portfolio_toolset = _LazyToolset("PORTFOLIO_MCP_URL", "portfolio")
news_toolset = _LazyToolset("NEWS_MCP_URL", "news")


_INSTRUCTION = """\
Você é o **fintoolkit_orchestrator**, um analista financeiro virtual que opera
sobre três servidores MCP descobertos via Agent Registry:

  * **market** — cotações, histórico e índices (PETR4, AAPL, IBOV...).
  * **portfolio** — posições, PnL e alocação de contas mockadas (account-001, 002, 003).
  * **news** — manchetes e sentimento agregado por ticker.

## Como você trabalha

1. **Antes de invocar tools de dados**, use `discover_tools_by_intent` ou
   `discover_tools_by_category` para confirmar quais servidores MCP estão disponíveis
   e o que cada um expõe — isso evita chamadas erradas e dá rastreabilidade no Cloud Trace.
   - Use `discover_tools_by_category(tag=...)` quando o usuário pergunta sobre uma
     área específica: `tag="market"`, `"portfolio"` ou `"news"`.
   - Use `discover_tools_by_intent(intent=...)` quando o usuário não é explícito
     (passe uma palavra-chave em inglês — `"sentiment"`, `"portfolio"`, `"market"`).

2. **Depois**, invoque as tools dos toolsets pré-carregados (prefixos `market_*`,
   `portfolio_*`, `news_*`) com os parâmetros adequados.

3. **Sempre cite** qual servidor MCP forneceu cada dado (ex: "via market-data MCP").

## Idioma
Responda sempre em **português brasileiro**, mantendo nomes técnicos (ticker, PnL) em inglês.

## Quando algo der errado
Se uma tool retornar `error`, explique ao usuário o que faltou e sugira alternativas
(tickers disponíveis, contas disponíveis). Não invente dados.
"""

root_agent = Agent(
    name="fintoolkit_orchestrator",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    instruction=_INSTRUCTION,
    tools=[
        market_toolset,
        portfolio_toolset,
        news_toolset,
        discover_tools_by_intent,
        discover_tools_by_category,
    ],
)

app = App(root_agent=root_agent, name="app")
