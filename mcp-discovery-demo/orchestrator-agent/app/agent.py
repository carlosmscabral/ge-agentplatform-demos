"""fintoolkit orchestrator — Financial analyst with dynamic MCP discovery.

Architecture:
  * Three MCP servers (market-data, portfolio, news-sentiment) run on Cloud Run.
  * deploy.sh registers each in Agent Registry and injects ONLY their registry
    resource names (`MARKET_MCP_NAME`, `PORTFOLIO_MCP_NAME`, `NEWS_MCP_NAME`)
    as env vars — URLs are NOT pre-baked. The Registry is the source of truth.
  * At module import time we resolve each toolset by calling
    `registry.get_mcp_toolset(name)`, which GETs the MCPServer resource and
    extracts `interfaces[].url`. The MCP server itself is contacted lazily by
    ADK on the first `get_tools()` call.
  * For LOCAL development (no Registry entries for localhost), each `*_MCP_URL`
    env var is used as a fallback when `*_MCP_NAME` is unset.
  * Two meta-tools (discover_tools_by_intent, discover_tools_by_category) call
    the Agent Registry to let the LLM introspect what's available.

Design note: we deliberately do NOT use a `_LazyToolset` wrapper here. That
wrapper is a useful production pattern when service availability at import
time is uncertain (deploy health checks, transient failures); this demo
favors simplicity and assumes Registry + MCP services are healthy. If you
need that resilience pattern, see `experimental/governance-demo/`.
"""

from __future__ import annotations

import logging
import os

import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from app.discovery import (
    build_toolset_from_registry,
    discover_tools_by_category,
    discover_tools_by_intent,
)

logger = logging.getLogger(__name__)

_, _project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", _project_id or "")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")


def _build_toolset(name_env_var: str, url_env_var: str, label: str) -> McpToolset:
    """Resolve a toolset preferring Registry lookup over hardcoded URL.

    1. If `<name_env_var>` is set → `registry.get_mcp_toolset(name)`. URL,
       prefix, and auth come from the Registry. Cloud path.
    2. Else if `<url_env_var>` is set → direct URL with `label` as
       `tool_name_prefix`. Local-dev path.
    3. Else → raise.
    """
    registry_name = os.environ.get(name_env_var, "").strip()
    if registry_name:
        logger.info("Resolving %s toolset via Registry: %s", label, registry_name)
        return build_toolset_from_registry(registry_name)

    direct_url = os.environ.get(url_env_var, "").strip()
    if direct_url:
        logger.info("Resolving %s toolset via direct URL (local dev): %s", label, direct_url)
        return McpToolset(
            connection_params=StreamableHTTPConnectionParams(url=direct_url),
            tool_name_prefix=label,
        )

    raise RuntimeError(
        f"Neither {name_env_var} (preferred, registry) nor {url_env_var} "
        f"(fallback, direct URL) is set — cannot build {label!r} toolset"
    )


market_toolset = _build_toolset("MARKET_MCP_NAME", "MARKET_MCP_URL", "market")
portfolio_toolset = _build_toolset("PORTFOLIO_MCP_NAME", "PORTFOLIO_MCP_URL", "portfolio")
news_toolset = _build_toolset("NEWS_MCP_NAME", "NEWS_MCP_URL", "news")


_INSTRUCTION = """\
Você é o **fintoolkit_orchestrator**, um analista financeiro virtual que opera
sobre servidores MCP descobertos dinamicamente via **Agent Registry**.

Os MCPs disponíveis (categorias usadas no Registry) são:

  * **market** — cotações, histórico e índices (PETR4, AAPL, IBOV...).
  * **portfolio** — posições, PnL e alocação de contas mockadas (account-001/002/003).
  * **news** — manchetes e sentimento agregado por ticker.

## Como você trabalha

1. **Antes de invocar tools de dados**, considere chamar `discover_tools_by_intent`
   ou `discover_tools_by_category` para confirmar quais servidores MCP estão disponíveis
   e o que cada um expõe — isso dá rastreabilidade no Cloud Trace e evita chamadas erradas.
   - `discover_tools_by_category(tag=...)` quando o usuário pergunta sobre uma área
     específica: `tag="market"`, `"portfolio"` ou `"news"`.
   - `discover_tools_by_intent(intent=...)` quando o usuário não é explícito (passe
     uma palavra-chave em inglês — `"sentiment"`, `"portfolio"`, `"market"`).

2. **Depois**, invoque as tools pelos nomes que a descoberta retornou. Os prefixos
   refletem o `displayName` no Registry (ex: tools de `market-data` ficam
   `market_data_*`; `portfolio` fica `portfolio_*`; `news-sentiment` fica
   `news_sentiment_*`).

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
