# LESSONS.md — Decisões, bugs e mudanças de rumo

Histórico das escolhas técnicas que levaram à implementação atual desta demo.
Cada seção descreve **o que tentamos primeiro, por que falhou, e como
chegamos no padrão final**. Este arquivo é registro — para o guia da
implementação atual, veja [`ARCHITECTURE.md`](./ARCHITECTURE.md).

Numeração não cronológica — organizada por temas para facilitar consulta.

---

## 1. Agent Registry não tem campo writable para tags

**Tentativa inicial**: `deploy.sh` chamava
`gcloud alpha agent-registry services create … --attributes="tag=market,domain=finance"`.

**Falha**: o CLI rejeitou com `unrecognized arguments: --attributes=...`.

**Investigação**: contra
`https://agentregistry.googleapis.com/$discovery/rest?version=v1alpha`:

- `MCPServer.attributes` é `readOnly` + reservado para o sistema. As únicas
  chaves válidas são `agentregistry.googleapis.com/system/RuntimeIdentity` e
  `…/system/RuntimeReference` — populadas pela plataforma, nunca pelo usuário.
- `Service` (envelope de criação) não tem `labels`, `tags`, nem `attributes`
  writable. O único free-form writable é `description` (até 2048 chars).
- Tentamos também injetar campos custom (`_meta`, `tags` no top-level) em
  `mcpServerSpec.content` — silenciosamente descartados pela API (validada
  contra o schema do `tools/list` do MCP).
- Por curiosidade testamos `Annotations.title` (o único string free-form
  per-tool no schema) — sobrevive, mas é semanticamente "título humano" e
  não escala para múltiplas tags.

**Decisão final**: codificar tags inline na `description` como marcadores
`[key:value]` e parsear client-side em `app/discovery.py:_parse_attributes`.
`experimental/governance-demo/` usa o mesmo padrão.

**Onde isso aparece hoje**:
- `deploy.sh` Step 4: `--description="[tag:market] [domain:finance] ..."`
- `app/discovery.py`: regex `\[(\w+):([^\]]+)\]` em `_parse_attributes`

---

## 2. `id_token.fetch_id_token()` não funciona em Agent Runtime

**Tentativa inicial**: primeira versão do `app/mcp_auth.py` mintava tokens
OIDC via:

```python
from google.oauth2 import id_token
token = id_token.fetch_id_token(request, audience=cloud_run_url)
```

Esse é o padrão canônico para Cloud Run → Cloud Run.

**Falha**: logs do agente mostraram:

```
Could not fetch URI /computeMetadata/v1/instance/service-accounts/default/identity?audience=…
Compute Engine Metadata server unavailable. Response status: 500
```

**Investigação**: a família `fetch_id_token` no `google.auth` é hardcoded
para usar o GCE metadata server (`http://metadata.google.internal/...`).
Agent Runtime não expõe metadata server, então não há fonte de onde minter
o ID token.

**Decisão (interim)**: mudar para enviar o access token SPIFFE-bound
diretamente como Bearer. Não resolveu o problema do Cloud Run IAM (veja §3),
mas eliminou o crash e deixou o token em logs do CR para forense.

**Onde isso aparece hoje**: o arquivo `mcp_auth.py` foi deletado quando
migramos para Option B (CR público + sem necessidade de header). A lição
permanece relevante para qualquer caso Agent Runtime → endpoint privado.

---

## 3. Sem Agent Gateway, não há caminho documentado para Agent Runtime → Cloud Run privado

**Tentativa**: deploy inicial usou Cloud Run com `--no-allow-unauthenticated`
e concedeu `roles/run.invoker` ao principal SPIFFE do orquestrador. Esperávamos
que o token bound do agente fosse aceito.

**Falha**: `HTTP/1.1 401 Unauthorized` em todas as chamadas.

**Investigação cruzada com o skill `agent-platform-debugger`**:

| Afirmação | Validade |
|---|---|
| Cloud Run IAM só aceita OIDC ID tokens, não OAuth access tokens | ✅ Verificado empiricamente (curl com access token = 401) |
| O token SPIFFE é DPoP-bound (atrelado ao cert X.509 via mTLS) | ✅ Documentado em `agent-identity.md` do skill |
| Cloud Run não tem mTLS terminator para validar o cert binding | ✅ Não existe `--mtls` ou similar em `gcloud run deploy` |
| `principal://<spiffe>` IAM funciona dentro do plano IAP (= Agent Gateway na frente) | ✅ A documentação do Agent Platform descreve exatamente este flow |
| Sem Gateway, nenhum mecanismo sancionado para autenticar | ✅ |

Cloud Run **não suporta SPIFFE/Agent Identity** hoje (2026-05) — apenas Agent
Runtime e Gemini Enterprise suportam. Managed Workload Identities (programa
SPIFFE mais amplo) suportam GKE Autopilot e Compute Engine em Preview, mas
não Cloud Run. Não existe flag `--agent-identity` em `gcloud run deploy`.

**Opções consideradas**:

| Opção | Custo | Pró | Contra |
|---|---|---|---|
| A. `--allow-unauthenticated` (escolhida) | Zero | Simples, funciona | Público na internet |
| B. App-layer auth (FastMCP middleware validando `tokeninfo`) | Modesto | Mantém CR público na rede mas valida principal | Latência extra; comportamento do tokeninfo com tokens DPoP-bound não validado |
| C. Migrar MCPs para GKE Autopilot + Managed Workload Identity | Alto | SPIFFE end-to-end | Refator grande; ops de GKE |
| D. Adicionar Agent Gateway | Médio | Caminho documentado, production-correct | Exige allowlist do projeto + infra adicional |

**Decisão final**: Opção A. O foco da demo é discovery dinâmica, não governance
de CR. `LESSONS` honestamente documenta a limitação.

**Onde isso aparece hoje**:
- `deploy.sh` Step 3: `--allow-unauthenticated` nas 3 Cloud Runs
- `deploy.sh` Step 8: comentário explicando por que NÃO há grants `principal://...` no CR
- `ARCHITECTURE.md` §6: tabela de IAM + nota "Por que Cloud Run é público nesta demo"

---

## 4. Strategy A — URLs em env vars deixava Registry decorativo

**Implementação inicial**: `deploy.sh` injetava 3 env vars no orquestrador:

```bash
MARKET_MCP_URL=https://fintoolkit-market-data-mcp-....run.app/mcp
PORTFOLIO_MCP_URL=https://fintoolkit-portfolio-mcp-...run.app/mcp
NEWS_MCP_URL=https://fintoolkit-news-sentiment-mcp-...run.app/mcp
```

O agente construía 3 `McpToolset` diretamente dessas URLs.

**Problema**: Agent Registry virou decorativo. Discovery retornava metadata
mas a LLM invocava toolsets que tinham sido pré-cabeados pelo deploy. Se uma
URL mudasse no Registry, o agente não percebia. A premissa "Registry como
source of truth para discovery" não era realidade.

**Decisão (intermediária — Strategy B)**: passar **nomes de recursos** do
Registry em vez de URLs, e usar `registry.get_mcp_toolset(name)` para
resolver a URL em runtime.

```bash
MARKET_MCP_NAME=projects/.../mcpServers/agentregistry-...
```

Validamos via logs do agente em runtime:
```
INFO: Resolving news toolset via Registry: projects/.../mcpServers/agentregistry-...
INFO: GET https://agentregistry.googleapis.com/v1alpha/.../mcpServers/<id> "200 OK"
```

Strategy B resolveu o problema das URLs mas levantou o próximo (§5).

---

## 5. Pré-carregar toolsets + discovery = discovery é decorativo (de novo)

**Observação do usuário (que levou ao refator final)**: mesmo com Strategy B,
o agente carregava 3 `McpToolset` no import e expunha discovery como
introspecção. A LLM via os 9 tools nominais (`get_stock_quote`,
`get_portfolio_holdings`, etc.) diretamente e podia invocá-los sem nunca
chamar discovery.

> "ao passar os 3 toolsets pro agente, além dos 2 de discovery, o agente/LLM
> já não tem toda a informação necessária para escolher as tools, sem
> precisar do discovery? qual o valor do discovery aqui?"

Resposta honesta: nenhum valor real. Discovery só servia para "rastreabilidade
no Cloud Trace" — pouca coisa.

**Decisão final — Option B (load-bearing discovery)**: substituímos os 3
toolsets pré-carregados por **um único roteador genérico**
`invoke_mcp_tool(mcp_server_name, tool_name, arguments)` que:

1. Resolve o `mcp_server_name` via `registry.get_mcp_server(name)` (cache)
2. Constrói o `McpToolset` no momento
3. Encontra a tool pelo nome
4. Invoca

A LLM agora **tem apenas 3 ferramentas** e **precisa** chamar discovery antes
de invocar qualquer coisa. Novos MCPs registrados após deploy ficam
imediatamente disponíveis.

**Trade-offs aceitos**:
- +1 turno da LLM por pergunta nova (discover + invoke) — +200-400 tokens
- Schemas de input das tools não são visíveis até discovery responder
- `invoke_mcp_tool` é um proxy/router, não um tool first-class no Trace

**Onde isso aparece hoje**: toda a `app/agent.py` final + `discovery.invoke_mcp_tool`.

---

## 6. Campos custom no toolspec são descartados

**Tentativa**: para evitar o workaround do `[tag:X]` na description (§1),
testamos injetar `_meta`, `tags`, e outras chaves custom dentro de
`mcpServerSpec.content` na chamada `services create`.

**Falha**: a API valida o conteúdo contra o schema do MCP `tools/list`.
Campos não reconhecidos são silenciosamente descartados — sem warning, sem erro.

**Reading back** com `gcloud alpha agent-registry mcp-servers describe`:

- `mcpServerSpec.content`: campos top-level desconhecidos sumiram.
- `Tool.annotations`: só as chaves padrão (`title`, `readOnlyHint`,
  `destructiveHint`, `idempotentHint`, `openWorldHint`) sobrevivem.
- `Annotations.title` é o único free-form string per-tool — mas semanticamente
  é "human-readable title", não tag.

**Decisão**: ficar com o workaround do `[tag:X]` na description (§1).

---

## 7. `_LazyToolset` vs. eager — quando cada um vale a pena

**Versão inicial deste demo (Strategy A e B)**: usávamos `_LazyToolset` —
wrapper `BaseToolset` que defere `McpToolset(...)` para o primeiro
`get_tools()`. Padrão importado do `experimental/governance-demo/`.

**Razão histórica**: Agent Runtime importa o módulo do agente durante deploy
health checks, **antes** que Registry ou MCP services estejam
necessariamente prontos. Eager construction falhava intermitentemente nesse
import.

**Quando simplificamos para eager (commit `2cc042a`)**: o foco da demo era a
discovery dinâmica, não resilience de deploy. Aceitamos que:
- Registry está saudável quando Step 6 do deploy roda
- Falha eager é mais alta e debugável ("crash no import") do que silent
  (`get_tools()` retorna vazio mid-conversation)

**Quando voltamos parcialmente** (introdução de cache em Option B): o
roteador `invoke_mcp_tool` cacheia `McpToolset` por `mcp_server_name`. Isso é
"lazy on demand" — só materializa quando a LLM realmente invoca. A primeira
invocação custa um GET ao Registry; as seguintes são instantâneas.

**Para produção**: copiar `_LazyToolset` do `experimental/governance-demo` se
você precisar resiliência ao import (deploy health checks com serviços
intermitentes). Hoje a demo não precisa.

**Onde isso aparece hoje**: `discovery._TOOLSET_CACHE` (dict process-local).
O `_LazyToolset` foi removido em `2cc042a` quando Option B foi implementada.

---

## 8. Gotcha de deploy: introspecção do `agents-cli` importa o agente localmente

**Sintoma**: `agents-cli deploy` falhou com:

```
RuntimeError: Neither MARKET_MCP_NAME (preferred, registry) nor MARKET_MCP_URL ...
```

Isso aconteceu **antes** do upload do tarball, em um subprocess local que
faz introspecção do agente.

**Causa**: enquanto o agente tinha eager toolset construction (Strategy B,
ANTES do Option B), o import requeria `*_MCP_NAME` env vars. O `--update-env-vars`
do `agents-cli` configura apenas o runtime — não a introspecção local.

**Solução**: `deploy.sh` exportava os env vars locally antes de invocar
`agents-cli deploy`. Hack feio mas funcional.

**Resolvido por Option B**: o novo agente não precisa de NENHUM env var
específico de MCP, então não há mais gotcha. O `deploy.sh` ficou mais limpo
sem o `export`.

---

## 9. `app/__init__.py` do scaffold re-exportava `agent.app`, quebrando testes

**Sintoma**: pytest collection falhava com o mesmo `RuntimeError` da §8 —
mas mesmo testes que só importam `discovery` (não `agent`).

**Causa**: `app/__init__.py` gerado pelo scaffold continha:
```python
from .agent import app
__all__ = ["app"]
```

Qualquer `from app import discovery` disparava `app/__init__.py` → import de
`app.agent` → eager toolset → crash sem env vars locais.

**Solução**: esvaziar `app/__init__.py`. Não há dependência externa do
re-export (`agent_runtime_app.py` usa `from app.agent import app as adk_app`,
caminho completo). Mudança não-invasiva.

---

## 10. `_normalize` lia tools do envelope de create, não do response do GET

**Sintoma silencioso**: discovery por keyword em tool name nunca encontrava
nada (ex: `intent="quote"` deveria achar `market-data` via `get_stock_quote`,
mas retornava `count: 0`).

**Causa**: `_normalize` lia tools de `server["mcpServerSpec"]["toolSpec"]["tools"]`
— esse path é o envelope que ENVIAMOS no create, não o que o Registry RETORNA.
Verificado via curl direto contra a API:

```bash
curl ... https://agentregistry.googleapis.com/v1alpha/.../mcpServers
# Retorna tools NO TOP-LEVEL:
# { "name": "...", "displayName": "...", "tools": [...], ... }
```

A API parsea o spec que enviamos e expõe os tools como campo top-level
read-only no recurso. Sem isso, `_normalize` retornava `tools: []` para todos
os servers, e a busca em tools nunca matchava.

**Fix**: trocar `server.get("mcpServerSpec", {}).get("toolSpec", {}).get("tools", [])`
por `server.get("tools", [])`. Uma linha.

**Lição**: sempre validar o **shape do response**, não assumir simetria com o
envelope de request. Especialmente em APIs alpha onde discovery doc pode ser
ambíguo.

---

## 11. Outras decisões menores

### Deploy paralelo dos 3 Cloud Runs

Cada `gcloud run deploy --source=.` faz rebuild completo (3-5 min). Sequencial
seriam 12-15 min. `deploy.sh` Step 3 lança os 3 em background subshells e
faz `wait` — ~3-4 min total.

### `tool_name_prefix=None` no roteador

`registry.get_mcp_toolset(name)` aplica prefix derivado do displayName
(`market-data` → tools viram `market_data_get_stock_quote`). No roteador
`invoke_mcp_tool`, queremos que `tool_name` aceito pela LLM seja o mesmo que
discovery retornou (cru: `get_stock_quote`). Por isso construímos
`McpToolset` direto com `tool_name_prefix=None`, bypassando o helper do
Registry.

### Sem GE registration (Rule #10 pulada)

O usuário explicitamente optou por não registrar no Gemini Enterprise para
esta demo. O `.env.template` tem o bloco opcional comentado, mas `deploy.sh`
não chama `agents-cli publish gemini-enterprise`.

### Testes integration do scaffold removidos

`tests/integration/test_agent.py` e `test_agent_runtime_app.py` foram gerados
pelo scaffold com prompts tipo "Why is the sky blue?" — não fazem sentido
para nosso agente (que não responde sem discovery + invoke). Apagados.

---

## Referências cruzadas

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — guia da implementação atual
- [`README.md`](./README.md) — quickstart + troubleshooting
- [`DEMO.md`](./DEMO.md) — roteiro de demo em PT-BR
- [`LEARNINGS.md` do repo root](../LEARNINGS.md) — padrões reutilizáveis em
  outras demos (FastMCP no CR, Registry tags, SPIFFE+CR)
- Skill `agent-platform-debugger` em `.claude/skills/` — referência
  autoritativa para o flow Agent Gateway + IAP
