# mcp-discovery-demo — Arquitetura

Guia técnico do **Financial Analyst Toolkit**: um agente ADK rodando no Agent
Runtime com identidade SPIFFE que descobre e invoca dinamicamente servidores
MCP via **Agent Registry**, sem conhecer nenhum deles em tempo de deploy.

Este documento descreve a implementação **atual** (Option B — descoberta como
única via de acesso). Para o histórico de decisões e padrões alternativos
considerados, veja [`LESSONS.md`](./LESSONS.md).

---

## 1. Visão geral

```
                         ┌──────────────────────────────────────────────┐
                         │             Agent Runtime (Vertex AI)        │
                         │                                              │
                         │   ┌────────────────────────────────────────┐ │
                         │   │  fintoolkit-orchestrator (ADK + SPIFFE)│ │
                         │   │                                        │ │
   usuário ──── ADK ───►│   │  3 tools (apenas):                     │ │
                         │   │   • discover_tools_by_intent   (FT)    │ │
                         │   │   • discover_tools_by_category (FT)    │ │
                         │   │   • invoke_mcp_tool            (FT)    │ │
                         │   │                                        │ │
                         │   │  cache local: dict[name → McpToolset] │ │
                         │   └─────────────────┬──────────────────────┘ │
                         └─────────────────────┼────────────────────────┘
                                               │
                ┌──────────────────────────────┼────────────────────────────┐
                │                              │                            │
                ▼                              ▼                            ▼
   ┌────────────────────────┐  ┌────────────────────────┐  ┌────────────────────────┐
   │   Agent Registry       │  │   Cloud Run (3 svcs)   │  │ Cloud Trace + Logs     │
   │                        │  │                        │  │                        │
   │  mcpServers/...        │  │  market-data-mcp       │  │ spans + payloads via   │
   │   ├ market-data        │  │  portfolio-mcp         │  │ OTEL_…_EVENT_ONLY      │
   │   ├ portfolio          │  │  news-sentiment-mcp    │  │                        │
   │   └ news-sentiment     │  │  (FastMCP 2.x,         │  └────────────────────────┘
   │  tags em description   │  │   Streamable HTTP,     │
   │  como `[tag:X]`        │  │   --allow-unauth…)     │
   └────────────────────────┘  └────────────────────────┘
```

**Idéia central**: o agente não sabe quais MCPs existem nem suas URLs. Para
qualquer pergunta que envolva dados, ele primeiro chama uma função de
descoberta para consultar o Registry e, em seguida, invoca a ferramenta
escolhida via um roteador genérico (`invoke_mcp_tool`).

---

## 2. As 3 ferramentas do agente

O orquestrador expõe ao Gemini exatamente três `FunctionTool`s. Não há nenhum
`McpToolset` pré-carregado — todo acesso a MCP passa pelo roteador.

### 2.1 `discover_tools_by_intent(intent: str)`

Busca por palavra-chave (case-insensitive substring) em **quatro lugares** de
cada MCPServer:

| Lugar | Exemplo de match |
|---|---|
| `display_name` | `intent="portfolio"` → bate em `displayName="portfolio"` |
| `description` | `intent="quotes"` → bate em `description="...quotes, history, indexes."` |
| `tool:<name>:name` | `intent="quote"` → bate em `tool.name="get_stock_quote"` |
| `tool:<name>:description` | `intent="holdings"` → bate em `tool.description="Account holdings"` |

Cada resultado inclui um campo `matched_in` (lista) explicitando **onde** o
match aconteceu — para que a LLM possa justificar sua escolha:

```json
{
  "criterion": "intent",
  "query": "quote",
  "count": 1,
  "matches": [
    {
      "name": "projects/.../mcpServers/agentregistry-...",
      "display_name": "market-data",
      "description": "[tag:market] [domain:finance] ...",
      "url": "https://.../mcp",
      "tools": [...],
      "attributes": {"tag": "market", "domain": "finance"},
      "matched_in": ["tool:get_stock_quote:name", "tool:get_stock_quote:description"]
    }
  ]
}
```

> **Por que não usar `searchMcpServers` do Registry?** A API REST tem esse
> método, mas seu `searchString` só conhece `mcpServerId | name | displayName`
> (verificado no discovery doc da `agentregistry.v1alpha`). Para buscar em
> nomes/descrições de tools precisamos do filtro client-side, ANTES de mais
> capacidades chegarem ao endpoint.

### 2.2 `discover_tools_by_category(tag: str)`

Filtra MCPs por categoria. Como `MCPServer.attributes` é `readOnly` no
Registry (veja [§4 — Forma de registro](#4-forma-de-registro-no-agent-registry)),
codificamos a categoria como marcador `[tag:X]` na `description` no momento do
registro (`deploy.sh` Step 4), e a função parseia esses marcadores
client-side.

```python
# discovery.py
_TAG_RE = re.compile(r"\[(\w+):([^\]]+)\]")
def _parse_attributes(description: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in _TAG_RE.finditer(description or "")}
```

Tags atuais: `market`, `portfolio`, `news` (mais `domain=finance` em todos).

### 2.3 `invoke_mcp_tool(mcp_server_name, tool_name, arguments)`

Roteador dinâmico — único caminho para a LLM efetivamente invocar uma tool.
Pseudocódigo:

```python
async def invoke_mcp_tool(mcp_server_name, tool_name, arguments, *, tool_context):
    toolset = _materialize_toolset(mcp_server_name)  # cache hit → instantâneo
    tools = await toolset.get_tools()
    target = next((t for t in tools if t.name == tool_name), None)
    if not target:
        return {"error": "...", "available_tools": [t.name for t in tools]}
    result = await target.run_async(args=arguments or {}, tool_context=tool_context)
    return {"result": result}
```

E `_materialize_toolset` (com cache process-local):

```python
_TOOLSET_CACHE: dict[str, McpToolset] = {}

def _materialize_toolset(mcp_server_name):
    if mcp_server_name in _TOOLSET_CACHE:
        return _TOOLSET_CACHE[mcp_server_name]
    server = registry.get_mcp_server(mcp_server_name)   # 1 GET ao Registry
    url = server["interfaces"][0]["url"]
    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=url),
        tool_name_prefix=None,    # nomes "crus" para casar com discovery
    )
    _TOOLSET_CACHE[mcp_server_name] = toolset
    return toolset
```

Detalhes importantes:

- **Sem prefixo de tool**: `tool_name_prefix=None` para que o nome retornado
  pela discovery (`get_stock_quote`) seja o mesmo aceito por `invoke_mcp_tool`.
  Se usássemos `registry.get_mcp_toolset()` (que aplica prefixo derivado do
  displayName), a LLM precisaria saber concatenar `market_data_get_stock_quote`.
- **Cache process-local de toolsets** — detalhado na próxima seção.
- **Erros explícitos**: se a tool não existe, devolve `available_tools` para a
  LLM tentar de novo com o nome certo.

---

## 2.4 Caching — o que é cacheado, o que não é, e por quê

A camada de descoberta + invocação dinâmica naturalmente bate no Agent
Registry com frequência. Para manter a latência razoável sem perder
frescura, o código tem **um único cache** com escopo bem definido. Esta
seção descreve exatamente o que é cacheado, qual é a chave, qual é o
tempo de vida, e por que NÃO cacheamos outras coisas.

### O que é cacheado

```python
# app/discovery.py
_TOOLSET_CACHE: dict[str, McpToolset] = {}

def _materialize_toolset(mcp_server_name: str):
    if mcp_server_name in _TOOLSET_CACHE:
        return _TOOLSET_CACHE[mcp_server_name]   # ← cache hit
    # ... cache miss path: GET registry + build McpToolset
    server_details = reg.get_mcp_server(mcp_server_name)   # 1 HTTP GET
    url = server_details["interfaces"][0]["url"]
    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=url),
        tool_name_prefix=None,
    )
    _TOOLSET_CACHE[mcp_server_name] = toolset
    return toolset
```

| Propriedade | Valor |
|---|---|
| **O que é** | `McpToolset` materializado (com URL já resolvida + connection params) |
| **Chave** | `mcp_server_name` (resource path completo: `projects/{P}/locations/{R}/mcpServers/agentregistry-<uuid>`) |
| **Escopo** | Processo único — `dict` Python em memória, não compartilhado entre instâncias |
| **Tempo de vida** | Vida do processo. Não há TTL, não há eviction policy |
| **Tamanho** | Ilimitado (na prática, ≤ número de MCPs únicos que a LLM invocou) |
| **Invalidação** | Nenhuma proativa. Restart da instância limpa tudo |
| **Thread safety** | Confia no GIL do Python — escritas são atômicas; race em cache-miss simultâneo pode gerar materializações duplicadas (idempotente, sem corromper) |

### O que NÃO é cacheado (deliberadamente)

| Operação | Onde acontece | Por que não cacheia |
|---|---|---|
| `list_mcp_servers()` em `discover_tools_by_*` | Toda chamada de discovery | Discovery precisa ser **fresca** — novos MCPs registrados aparecem na próxima descoberta sem reiniciar o agente. É o argumento principal da demo. |
| `get_mcp_server(name)` em cache miss | Só na primeira materialização de cada MCP | Cacheado **indiretamente** via `_TOOLSET_CACHE` — uma vez que o toolset está construído, esse GET não acontece de novo. |
| Resposta de tool MCP (`get_stock_quote("AAPL")`) | Toda invocação | Tools podem mudar (cotação, sentimento). Cache aqui seria responsabilidade do MCP server, não do orquestrador. |
| Sessão MCP (Streamable HTTP) | Reconectada a cada turno | O `McpToolset` mantém connection params mas o `mcp.client` session é stateless por design da implementação ADK. |

### Fluxo com cache (sequência real, primeira vs segunda chamada)

```
─── Primeira invocação de market-data nesta instância ─────────────────────
LLM ──► invoke_mcp_tool(name="...mcpServers/agentregistry-2bf9...",
                        tool_name="get_stock_quote", args={"ticker":"AAPL"})
                │
                ▼
        _materialize_toolset(name)
                │
                ▼
        cache_lookup("...agentregistry-2bf9...") → MISS
                │
                ▼
        registry.get_mcp_server(name)
                │  HTTP GET https://agentregistry.googleapis.com/v1alpha/.../mcpServers/agentregistry-2bf9...
                ▼
        server_details["interfaces"][0]["url"] → "https://fintoolkit-market-data-mcp-...run.app/mcp"
                │
                ▼
        McpToolset(connection_params=..., tool_name_prefix=None)
                │
                ▼
        _TOOLSET_CACHE["...agentregistry-2bf9..."] = toolset
                │  LOG: "Materialized + cached toolset for ... → ..."
                ▼
        toolset.get_tools()   →   conecta ao Cloud Run, lista tools
        target.run_async(args={"ticker":"AAPL"}, tool_context=...)
        return {"result": {...}}

─── Segunda invocação de market-data (mesma instância) ────────────────────
LLM ──► invoke_mcp_tool(name="...agentregistry-2bf9...",
                        tool_name="get_historical_prices", args={...})
                │
                ▼
        _materialize_toolset(name)
                │
                ▼
        cache_lookup("...agentregistry-2bf9...") → HIT  (≈0ms)
                │
                ▼  (sem GET ao Registry, sem reconstrução do toolset)
        toolset.get_tools()   →   conecta ao Cloud Run
        target.run_async(args={...}, tool_context=...)
        return {"result": {...}}
```

### Evidência empírica (validado em produção)

Após emitir 2 requests consecutivas que usam o mesmo MCP `market-data`:

1. `"Qual a cotação atual da GOOGL?"` → invoca `get_stock_quote`
2. `"Agora me dê o histórico de 7 dias da GOOGL"` → invoca `get_historical_prices`

Os logs do Cloud Run (`resource.type=ReasoningEngine`) mostram exatamente:

| Operação | Quantidade | Comentário |
|---|---|---|
| `Materialized + cached toolset for ...market-data...` | **0** novas | `market-data` já estava no cache desde uma execução anterior |
| `GET .../mcpServers/agentregistry-...2bf9...` (URL resolution) | **0** | Confirmou cache hit |
| `GET .../mcpServers` (list — discovery) | **3** | Uma por discovery feita pela LLM (Request 1 + Request 2 fizeram discovery cada uma) |

Comparando com o teste E2E inicial (3 atos com cold cache):

| MCP | Materializações no total | Invocações no total |
|---|---|---|
| `market-data` | 1 | 2 (Act 3 + GOOGL teste subsequente) |
| `portfolio` | 1 | 1 |
| `news-sentiment` | 1 (no Act 1) | 2 (Act 1 + Act 3) |

**1 materialização por MCP, N invocações** — exatamente o comportamento
esperado de um cache write-once.

### Quando esse cache é (e não é) suficiente

**Suficiente para esta demo porque**:

- Cada instância do Agent Runtime serve várias requests dentro da janela de
  `Min Instances=1` (24h+ típicos antes de reciclar).
- As URLs dos Cloud Run são estáveis dentro de um deploy.
- O custo de re-materializar (1 GET + construção de objeto) é < 200ms — não
  vale a complexidade de adicionar TTL.

**NÃO suficiente em produção quando**:

| Cenário | Problema | Mitigação |
|---|---|---|
| URL do MCP muda no Registry sem redeploy do agente | Cache tem URL stale → invocação falha | Adicionar TTL curto (ex: 5–15 min) OU invalidar no primeiro erro de conexão |
| Restart frequente da instância (autoscaling agressivo) | Cache se esvazia frequentemente, todo turno paga 1 GET | Acceptable; é o que o cache resolve dentro de cada vida útil |
| Muitos MCPs (10s) com baixa frequência de uso de cada | Cache pode crescer e ocupar memória | Trocar `dict` por `cachetools.LRUCache(maxsize=N)` |
| Múltiplas réplicas → cache não compartilhado | Cada réplica paga seu próprio cold cache | Move para um cache compartilhado (Memorystore Redis) ou aceita o custo |
| `tool_context` precisa propagar identidade do usuário pra Cloud Run | O `McpToolset` cacheado é compartilhado entre sessões | Cache hoje não tem essa preocupação porque MCPs são públicos e stateless; com auth real seria necessário cachear por `(name, principal)` |

A implementação atual é **deliberadamente o mais simples possível** — uma
dict, sem TTL, sem invalidação, sem locking. O comentário no código aponta
exatamente para essas opções avançadas se forem necessárias.

---

## 3. Fluxo end-to-end

Sequência típica para a pergunta "qual o sentimento sobre a Apple?":

```
usuário ──► "qual o sentimento sobre a Apple?"
              │
              ▼
        ┌───────────────────────────────────────┐
        │ Gemini: preciso descobrir ferramentas │
        │ relacionadas a "sentimento"           │
        └────────────────────┬──────────────────┘
                             │
                             ▼
          discover_tools_by_intent(intent="sentiment")
                             │
                             ▼
        ┌──────────────────────────────────────────────────┐
        │  AgentRegistry.list_mcp_servers()                │
        │    GET agentregistry.googleapis.com/v1alpha/...  │
        │  (retorna 3 MCPs com tools embutidos no payload) │
        │                                                  │
        │  filtra client-side por "sentiment" em:          │
        │    displayName, description, tools[*].name|desc  │
        │                                                  │
        │  → news-sentiment (matched_in=                   │
        │      ["tool:get_sentiment_score:name",           │
        │       "display_name"])                           │
        └────────────────────┬─────────────────────────────┘
                             │
                             ▼
        ┌───────────────────────────────────────┐
        │ Gemini: matches[0].tools tem          │
        │ get_sentiment_score(ticker) — vou     │
        │ invocá-lo                             │
        └────────────────────┬──────────────────┘
                             │
                             ▼
        invoke_mcp_tool(
            mcp_server_name="projects/.../mcpServers/agentregistry-...",
            tool_name="get_sentiment_score",
            arguments={"ticker": "AAPL"}
        )
                             │
                             ▼
        ┌──────────────────────────────────────────────────┐
        │  _materialize_toolset(name)                      │
        │    cache miss → GET .../mcpServers/<name>        │
        │    cria McpToolset com URL extraída              │
        │    cacheia                                       │
        │  toolset.get_tools() → conecta no Cloud Run      │
        │    (Streamable HTTP, JSON-RPC initialize)        │
        │  target.run_async(args={"ticker": "AAPL"}, ...)  │
        │    POST .../mcp (JSON-RPC tools/call)            │
        └────────────────────┬─────────────────────────────┘
                             │
                             ▼
        ┌────────────────────────────────────────────┐
        │  FastMCP 2.x no Cloud Run                  │
        │  • público (--allow-unauthenticated)       │
        │  • @mcp.tool() get_sentiment_score executa │
        │    sobre o dict mockado SENTIMENT          │
        │  • retorna {"ticker":"AAPL","score":0.78,  │
        │             "label":"very_positive", ...}  │
        └────────────────────┬───────────────────────┘
                             │
                             ▼
        Gemini compõe a resposta em PT-BR citando "via news-sentiment MCP"
                             │
                             ▼
        usuário ◄── resposta
```

Latência típica observada (cold cache, primeira invocação de uma sessão):

| Etapa | Tempo aproximado |
|---|---|
| Turno 1 (discover) | 1.5–3 s (1 Gemini call + 1 list_mcp_servers) |
| Turno 2 (invoke, cache miss) | 2–4 s (1 Gemini call + 1 get_mcp_server + 1 MCP initialize + 1 tool call) |
| Turno 2 (invoke, cache hit) | 1–2 s (1 Gemini call + 1 MCP tool call) |

---

## 4. Forma de registro no Agent Registry

O que `gcloud alpha agent-registry mcp-servers describe <name>` retorna:

```yaml
name: projects/{P}/locations/{R}/mcpServers/agentregistry-<uuid>
displayName: market-data
description: '[tag:market] [domain:finance] market-data MCP server for the fintoolkit demo.'
interfaces:
  - protocolBinding: JSONRPC                  # JSON-RPC sobre Streamable HTTP
    url: https://fintoolkit-market-data-mcp-…run.app/mcp
tools:                                        # populado pela plataforma a partir do create
  - name: get_stock_quote
    description: Get latest quote (price, change, volume) ...
    annotations: {readOnlyHint: true, idempotentHint: true}
  - name: get_historical_prices
    description: ...
  - name: get_market_index
    description: ...
attributes:                                   # readOnly — só a plataforma escreve aqui
  agentregistry.googleapis.com/system/RuntimeReference:
    uri: //agentregistry.googleapis.com/projects/.../services/fintoolkit-market-data-mcp
```

### Por que tags vão em `description`, não em `attributes`?

`MCPServer.attributes` é **readOnly + system-reserved** na
`agentregistry.v1alpha` API. As únicas chaves válidas são `…/system/RuntimeIdentity`
(populada apenas quando o runtime tem Agent Identity — Cloud Run não tem hoje)
e `…/system/RuntimeReference`. Não existe campo `labels` nem `tags` writable em
`Service` ou `MCPServer`. O único free-form writable é `description` (até 2048
chars). Veja [`LESSONS.md` §1](./LESSONS.md) para os detalhes desta descoberta.

### Cada MCP é registrado assim (deploy.sh Step 4):

```bash
gcloud alpha agent-registry services create fintoolkit-market-data-mcp \
    --location=us-central1 \
    --display-name="market-data" \
    --description="[tag:market] [domain:finance] market-data MCP server ..." \
    --interfaces="protocolBinding=jsonrpc,url=https://.../mcp" \
    --mcp-server-spec-type=tool-spec \
    --mcp-server-spec-content="$(cat market-data-mcp/toolspec.json)"
```

O `toolspec.json` (uma fonte da verdade por MCP) deve espelhar os
`@mcp.tool()` definidos em `app/main.py`. Não vamos colocar campos custom em
`toolspec.json` — eles são silenciosamente descartados pela API (veja
[`LESSONS.md` §6](./LESSONS.md)).

---

## 5. Os 3 servidores MCP

Todos os três seguem o mesmo padrão: **FastMCP 2.x + Streamable HTTP** no
Cloud Run, dados mockados em memória (zero dependência externa).

### 5.1 Estrutura comum

```
<mcp-name>/
├── pyproject.toml           # deps: fastmcp>=2.13
├── Dockerfile               # python:3.12-slim + uv sync + uv run
├── toolspec.json            # schema espelhando os @mcp.tool()
├── app/
│   ├── __init__.py
│   ├── main.py              # FastMCP server bindando $PORT em /mcp
│   └── fake_data.py         # dicionários mockados (QUOTES, HOLDINGS, NEWS...)
└── tests/
    ├── test_tools.py        # FastMCP in-memory Client
    └── test_http_wire.py    # subprocess + Client(url) — só market-data
```

### 5.2 Pattern do `main.py`

```python
import os
from fastmcp import FastMCP
from app.fake_data import QUOTES

mcp = FastMCP(name="fintoolkit-market-data")

@mcp.tool()
def get_stock_quote(ticker: str) -> dict:
    """Get latest quote (price, change, volume) for a stock ticker."""
    return QUOTES.get(ticker.upper(), {"error": "ticker not found"})

# ... mais @mcp.tool()s

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
```

### 5.3 Dockerfile (uv + python:3.12-slim)

```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
CMD ["uv", "run", "python", "-m", "app.main"]
```

### 5.4 Catálogo de tools

| MCP | Tag | Tools |
|---|---|---|
| `market-data` | `market` | `get_stock_quote`, `get_historical_prices`, `get_market_index` |
| `portfolio` | `portfolio` | `get_portfolio_holdings`, `get_position_pnl`, `get_portfolio_allocation` |
| `news-sentiment` | `news` | `get_company_news`, `get_sentiment_score`, `search_news` |

Todas read-only (mock data). Contas mockadas: `account-001`, `account-002`,
`account-003`. Tickers cobertos: PETR4, VALE3, ITUB4, AAPL, GOOGL, MSFT.

---

## 6. Identidade e modelo de IAM

```
deploy.sh Step 2
    │
    ▼
SPIFFE principal SET (todos os agentes deste projeto)
    principalSet://agents.global.{org|project}-…/attribute.platformContainer/…
    │
    ▼ baseline roles concedidos UMA vez
      ✓ roles/aiplatform.agentDefaultAccess
      ✓ roles/aiplatform.user
      ✓ roles/serviceusage.serviceUsageConsumer
      ✓ roles/logging.logWriter
      ✓ roles/monitoring.metricWriter
      ✓ roles/cloudapiregistry.viewer
      ✓ roles/storage.objectAdmin
      ✓ roles/agentregistry.viewer       ◄── obrigatória para discovery
    │
    ▼
deploy.sh Step 6: agents-cli deploy --agent-identity
    │  Agent Runtime emite cert SPIFFE (X.509, 24h, auto-rotacionado)
    ▼
SPIFFE PRINCIPAL específico (este orquestrador)
    principal://agents.global.…/resources/aiplatform/…/reasoningEngines/<id>
    │  herda todas as roles do principalSet acima
    ▼
Cloud Run (3 MCPs) — public (--allow-unauthenticated)
    │  agent envia bearer = SPIFFE access token
    │  Cloud Run ignora (público); FastMCP atende
    │  (token segue útil em CR access logs e para futura camada de auth)
```

### Tabela completa de bindings IAM criados

| Escopo | Principal | Role | Para quê |
|---|---|---|---|
| Projeto | `principalSet://agents.global.{org\|project}-…/attribute.platformContainer/aiplatform/projects/{N}` | `roles/aiplatform.agentDefaultAccess` | Capacidades baseline do agente |
| Projeto | mesmo principalSet | `roles/aiplatform.user` | Inferência, sessions |
| Projeto | mesmo principalSet | `roles/serviceusage.serviceUsageConsumer` | Quota do projeto |
| Projeto | mesmo principalSet | `roles/logging.logWriter` | Escrever logs |
| Projeto | mesmo principalSet | `roles/monitoring.metricWriter` | Emitir métricas |
| Projeto | mesmo principalSet | `roles/cloudapiregistry.viewer` | Ler Cloud API Registry |
| Projeto | mesmo principalSet | `roles/storage.objectAdmin` | Bucket de staging |
| Projeto | mesmo principalSet | `roles/agentregistry.viewer` | **Obrigatória** para `AgentRegistry.list_mcp_servers()` e `get_mcp_server()` |
| Cada Cloud Run service | `allUsers` | `roles/run.invoker` | Acesso público (veja [`LESSONS.md` §3](./LESSONS.md)) |

### O que quebra se você pular algum passo

| Se faltar… | Sintoma |
|---|---|
| `agentregistry.viewer` no principalSet SPIFFE | Discovery retorna `count: 0`; agente fica "cego" |
| `allUsers:run.invoker` em algum CR | `invoke_mcp_tool` retorna `{"error": "...401..."}`; tool fica inalcançável |
| `--agent-identity` no `agents-cli deploy` | Agente usa SA padrão do Reasoning Engine; principalSet IAM não se aplica → discovery 401 |
| `uv lock` antes do `agents-cli deploy` após mudar `pyproject.toml` | `agents-cli` falha: `uv export --locked: lockfile is out of date` |

### Por que Cloud Run é público nesta demo

Cloud Run não suporta SPIFFE/Agent Identity hoje (apenas Agent Runtime e
Gemini Enterprise suportam). Sem Agent Gateway no meio, **não há caminho
documentado** para um agente SPIFFE em Agent Runtime autenticar contra um
Cloud Run privado. Detalhes completos da investigação em
[`LESSONS.md` §3](./LESSONS.md).

A identidade SPIFFE continua sendo a fronteira de segurança **do lado do
agente** — o Registry só responde para chamadores autenticados, e o agente
está autenticado via SPIFFE.

---

## 7. Loop de desenvolvimento local-first

Sequência recomendada (Regra #3 do repo):

```
Unit tests dos MCPs (FastMCP in-memory Client)
       │
       ▼ verde
HTTP wire smoke do market-data (subprocess + Client(url))
       │
       ▼ verde
local_test.py — sobe os 3 MCPs simultaneamente, prova end-to-end local
       │
       ▼ verde
Unit tests do orquestrador (discovery + invoker, AgentRegistry mockado)
       │
       ▼ verde
./deploy.sh  ← só aqui começa a custar cloud
       │
       ▼ verde
agents-cli run --url <orch-cloud-url> --mode adk "..."
       │
       ▼ rastros validados no Cloud Trace
done
```

### Por que não temos integration test "online"

Os scaffolds do `agents-cli` geram um `tests/integration/test_agent.py` que
faz um turno real contra o modelo. Removemos esses testes porque:

- Requerem credenciais Gemini configuradas, billable
- São flakey (dependem do output do modelo)
- A camada valiosa — lógica de discovery — já é coberta por
  `tests/unit/test_discovery.py` com o Registry mockado

O "real integration test" é a inspeção do Cloud Trace pós-`./deploy.sh`. Veja
[`DEMO.md`](./DEMO.md).

---

## 8. Verificação no Cloud Trace

Para cada interação `discover → invoke`, espere ver no trace:

```
fintoolkit_orchestrator (root span)
├── gemini_generate_content              [primeiro turno: decide chamar discover]
├── discover_tools_by_intent             [FunctionTool span]
│   └── http: GET .../mcpServers          [AgentRegistry SDK]
├── gemini_generate_content              [segundo turno: decide chamar invoke]
└── invoke_mcp_tool                       [FunctionTool span]
    ├── http: GET .../mcpServers/<name>  [cache miss → resolve URL]
    └── mcp_call (tools/call)             [via McpToolset → POST .../mcp]
```

Se a busca acerta no cache do toolset (segunda invocação em diante para o mesmo MCP),
o `GET .../mcpServers/<name>` desaparece — fica só `mcp_call`.

Cobertura de payloads (prompts, respostas, args, returns) está habilitada via
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY` (set por
`deploy.sh` Step 6 — Regra #4 do repo).

---

## Referências cruzadas

- [`README.md`](./README.md) — quickstart, prerequisitos, troubleshooting
- [`DEMO.md`](./DEMO.md) — roteiro de demo em PT-BR com prompts copy-paste
- [`LESSONS.md`](./LESSONS.md) — histórico de decisões, bugs, alternativas
- [`LEARNINGS.md` do repo root](../LEARNINGS.md) — padrões reutilizáveis
  validados nesta demo (FastMCP no CR, Registry tags, SPIFFE+CR)
- [`CLAUDE.md` do repo root](../CLAUDE.md) — as 11 regras de produção
