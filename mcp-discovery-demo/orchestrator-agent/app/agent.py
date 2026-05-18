"""fintoolkit orchestrator — Dynamic MCP discovery with Agent Registry.

Architecture (Option B — fully dynamic):
  * The agent has ZERO knowledge of specific MCP servers at deploy time.
    No `*_MCP_NAME` env vars. No pre-loaded toolsets.
  * Three ADK function tools, all backed by `app/discovery.py`:
      - `discover_tools_by_intent(intent)`   — substring search across
        displayName, description, and each tool's name/description.
      - `discover_tools_by_category(tag)`    — filter by `[tag:X]` markers
        encoded in the MCPServer description (Registry has no writable
        attributes — see ARCHITECTURE.md §5).
      - `invoke_mcp_tool(mcp_server_name, tool_name, arguments)` — looks up
        the MCPServer in the Registry, materializes a (cached) McpToolset,
        finds the named tool, and invokes it with the provided args.
  * The LLM MUST go through `discover_*` first to learn what's available.
    `invoke_mcp_tool` is the only path to actually call a tool.
  * New MCP servers registered in Agent Registry after deploy are
    discoverable and invokable without re-deploying the agent.
"""

from __future__ import annotations

import logging
import os

import google.auth
from google.adk.agents import Agent
from google.adk.apps import App

from app.discovery import (
    discover_tools_by_category,
    discover_tools_by_intent,
    invoke_mcp_tool,
)

logger = logging.getLogger(__name__)

_, _project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", _project_id or "")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")


_INSTRUCTION = """\
Você é o **fintoolkit_orchestrator**, um analista financeiro virtual.
Você opera sobre servidores MCP descobertos dinamicamente via **Agent Registry** —
você NÃO tem conhecimento prévio de quais MCPs ou tools existem.

Você tem exatamente **3 ferramentas**:

1. `discover_tools_by_intent(intent: str)` — busca MCPs cujo nome, descrição,
   ou nome/descrição de alguma tool contém a palavra-chave. Retorna `matched_in`
   indicando onde o match aconteceu.
2. `discover_tools_by_category(tag: str)` — filtra MCPs por categoria (`market`,
   `portfolio`, `news`, ...).
3. `invoke_mcp_tool(mcp_server_name, tool_name, arguments)` — executa uma tool
   de um MCP específico. Use os valores `name` (do MCP) e `tools[].name`
   retornados pelo discovery — sem prefixos.

## Fluxo obrigatório

Para qualquer pergunta do usuário que envolva dados:

1. **Descubra**: chame `discover_tools_by_intent` ou `discover_tools_by_category`
   com um keyword relevante. Examine `matches[].name`, `matches[].tools[]`, e
   `matches[].matched_in`.
2. **Invoque**: chame `invoke_mcp_tool` com:
   - `mcp_server_name` = `matches[i].name` (caminho completo de recurso)
   - `tool_name` = `matches[i].tools[j].name` (sem prefixo)
   - `arguments` = dict com os argumentos esperados pela tool (verifique a
     descrição da tool em `tools[j].description`).
3. **Componha** a resposta para o usuário citando o MCP de origem.

Se uma chamada retornar `{"error": ...}`, leia a mensagem e tente outra
abordagem (ex: `available_tools` lista o que existe, sugerindo correção).

## Múltiplos MCPs
Para perguntas que combinam dados (ex: PnL + cotação + notícia), faça discovery
uma vez (ou por categoria) e invoke múltiplas vezes em sequência.

## Idioma
Responda sempre em **português brasileiro**. Mantenha termos técnicos
(ticker, PnL, MCP) em inglês.

## Política
Não invente dados. Se a discovery não retorna o MCP esperado, diga isso
explicitamente. Cite o `mcp_server_name` (ou ao menos o displayName) na
resposta para rastreabilidade.
"""

root_agent = Agent(
    name="fintoolkit_orchestrator",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    instruction=_INSTRUCTION,
    tools=[
        discover_tools_by_intent,
        discover_tools_by_category,
        invoke_mcp_tool,
    ],
)

app = App(root_agent=root_agent, name="app")
