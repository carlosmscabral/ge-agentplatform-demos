# LESSONS.md — Decisões e bugs encontrados

Histórico das escolhas técnicas, bugs descobertos durante o build, e
alternativas consideradas e descartadas. Para o guia da implementação
atual, veja [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## 0. Resumo executivo: a história em uma frase

**Saímos** para construir um demo de **Agent Engine Code Execution Sandbox**
(produto `agent_engines.sandboxes`, doc
[scale/sandbox/code-execution-overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/sandbox/code-execution-overview))
via `AgentEngineSandboxCodeExecutor` do ADK. Após investigação extensa
(§§1-11), descobrimos que **Gemini 2.5+ bypassa esse caminho** usando
sua code execution nativa (§12). **Pivotamos** para
`BuiltInCodeExecutor` (= **Gemini API Code Execution**, doc
[vertex-ai/.../multimodal/code-execution](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/code-execution)),
que é um produto **distinto** mas funcionalmente similar (mesmo gVisor,
mesmas restrições de rede/pacotes), só com lifecycle 100% gerenciado
pela Gemini API em vez do Agent Engine resources API.

Trade-off aceito:
- ✅ Funciona ponta-a-ponta com Gemini 2.5+
- ✅ Mesma postura de segurança (sandbox isolado, sem rede, sem install)
- ❌ Perdemos controle de TTL/listagem (era um pilar da demo original)
- ❌ Nome da demo (`code-execution-demo`) ficou ambíguo

**Trabalho futuro** (não bloqueia esta demo): para realmente usar
`AgentEngineSandboxCodeExecutor` end-to-end, seria necessário um
`before_model_callback` no Agent que strip os parts
`executable_code`/`code_execution_result` da response do Gemini antes
que ADK's `_run_post_processor` veja, forçando ADK a achar que código
"ainda não foi executado" → roteia para nosso executor. Estimativa:
~50 linhas de código numa subclass + risk de fragilidade contra
updates da ADK. Documentado para futuro investigação.

---

## 1. Escolha original do executor — `AgentEngineSandboxCodeExecutor`

Avaliamos 4 opções via leitura direta do ADK
(`analyst-agent/.venv/lib/python3.12/site-packages/google/adk/code_executors/`):

| Executor | Stateful | Backend | Por que NÃO escolhido |
|---|---|---|---|
| `BuiltInCodeExecutor` | ❌ | Sandbox do Gemini API | Conta a história do **Gemini**, não do **Agent Runtime**. Stateless por execução. |
| `VertexAiCodeExecutor` | ✅ | Code Interpreter Extension (legacy) | Superseded por `AgentEngineSandbox...` per docs do ADK. |
| `GkeCodeExecutor` | ✅ | gVisor on GKE | Exige cluster GKE pré-existente. Overhead operacional alto para uma demo. |
| **`AgentEngineSandboxCodeExecutor`** | ✅ | Agent Engine managed sandbox | **Escolhido** — purpose-built para Agent Runtime, ~40 libs, sem rede, sem package install, audit trail nativo. |

A capability é exatamente o que a demo quer mostrar: code execution
**purpose-built para Agent Runtime**, não para o Gemini API em geral.

---

## 2. Chicken-and-egg: o `agent_engine_resource_name`

`AgentEngineSandboxCodeExecutor.__init__` aceita 3 modos
(`agent_engine_sandbox_code_executor.py` linhas 53-103):

| Modo | Args passados | Comportamento |
|---|---|---|
| 1 | `sandbox_resource_name="projects/…/sandboxEnvironments/789"` | Usa sandbox pré-existente |
| 2 | (nada) | **Auto-cria** Agent Engine novo na primeira `execute_code` (line 122) |
| 3 | `agent_engine_resource_name="projects/…/reasoningEngines/456"` | Cria sandboxes embaixo deste RE |

**O problema com Modo 2**: cada réplica do orquestrador (Agent Runtime
pode escalar para N instâncias) criaria SEU PRÓPRIO Agent Engine na
primeira invocação — proliferação descontrolada de REs órfãos no projeto,
custo extra, e a console fica confusa (vários "default" REs aparecendo).

**Decisão (Modo 3)**: pre-criar UM Reasoning Engine dedicado ("sandbox
host") em `deploy.sh` Step 4 e injetar seu resource name via env var
`AGENT_ENGINE_RESOURCE_NAME` no orquestrador.

Implementação:
```bash
# deploy.sh Step 4 (resumido)
# Idempotência: lista REs via REST, reusa se existe por displayName
EXISTING=$(curl -s ".../reasoningEngines?pageSize=200" | python3 -c "filter...")
if [ -z "${EXISTING}" ]; then
    SANDBOX_HOST=$(uv --directory analyst-agent run python - <<EOF
import vertexai
client = vertexai.Client(project=..., location=..., http_options={"api_version": "v1beta1"})
r = client.agent_engines.create(config={"display_name": "..."})
print(r.api_resource.name)
EOF
    )
fi
```

`undeploy.sh` deleta tanto o orquestrador quanto o sandbox-host RE.

---

## 3. `python3` system não tem `vertexai` (bug pego no primeiro deploy)

**Sintoma**: primeira execução do `deploy.sh` falhou no Step 4 com:
```
ModuleNotFoundError: No module named 'vertexai'
```

**Causa**: o heredoc Python no Step 4 invocava `python3` (system Python),
que não tem `vertexai` instalado. A SDK só está no venv do `analyst-agent`.

**Fix**: substituir por `uv --directory analyst-agent run python -`. Isso
usa o venv do analyst-agent (que tem vertexai via
`google-cloud-aiplatform[agent-engines]`).

**Lição**: sempre que rodar Python "off-script" em deploy.sh, usar o venv
explicitamente — system Python é deliberadamente minimal.

---

## 4. `gcloud beta ai reasoning-engines` não existe

**Sintoma**: tentativa de listar REs via gcloud:
```
$ gcloud beta ai reasoning-engines list ...
ERROR: (gcloud.beta.ai) Invalid choice: 'reasoning-engines'.
Maybe you meant: gcloud beta ai semantic-governance-policies list
```

**Causa**: o repo-level `LEARNINGS.md` linha 157 já documenta isso —
**não existe `gcloud ai reasoning-engines`**. As únicas opções são:
- `agents-cli deploy`/`deploy --status`/`deploy --list`
- Python SDK (`vertexai.agent_engines`)
- REST API direta

**Fix em deploy.sh**: substituir o `gcloud beta ai reasoning-engines list`
por curl direto ao REST:
```bash
curl -s "https://${REGION}-aiplatform.googleapis.com/v1beta1/.../reasoningEngines?pageSize=200" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" | python3 -c "filter for displayName"
```

**Lição**: para qualquer operação em RE que não seja `agents-cli deploy`,
caia direto no REST API. Não há atalho via gcloud.

---

## 5. Per-RE IAM grant é redundante

**Tentativa inicial**: `deploy.sh` Step 7 adicionava explicitamente
`roles/aiplatform.user` ao principal SPIFFE do orquestrador NO sandbox-host
RE via `gcloud beta ai reasoning-engines add-iam-policy-binding`.

**Problemas**:
1. O comando não existe (vide §4)
2. O Step 2 já concede `roles/aiplatform.user` a nível de projeto ao
   principalSet SPIFFE. Como esse role é project-wide, o orquestrador já
   tem acesso a `agent_engines.sandboxes.*` em qualquer RE do projeto —
   incluindo o sandbox-host

**Fix**: removido o Step 7. Apenas verifica que o SPIFFE existe (foi
provisionado) e imprime uma nota explicando que o IAM project-wide cobre
o caso.

**Lição**: comece sempre com IAM no escopo mais amplo (`projects/`) — só
adicione per-recurso quando há motivo concreto (least privilege real).

---

## 6. Sessões + sandbox state

Lendo `execute_code()` linhas 130-167:
- `session.state['sandbox_name']` é o cache do sandbox para essa sessão
- Próximas invocações na MESMA SESSÃO reusam o sandbox (variáveis persistem)
- Sessão nova → sandbox novo
- TTL do sandbox: 1 ano (configurado pelo executor por padrão)
- Estado interno do interpretador Python perde-se se sandbox ficar 14 dias
  sem uso (mesmo dentro do TTL)

**Implicação para a demo (DEMO.md Ato 1)**: usar `--session-id` explícito
para que os 3 turnos compartilhem sandbox.

**O que `agents-cli scaffold` fez**: o scaffold `--deployment-target
agent_runtime` configura `AgentEngineApp` com session service padrão do
Agent Runtime, que persiste session state em backend gerenciado. **Tentei**
passar `--session-type agent_platform_sessions` mas o CLI rejeitou:
```
Error: --session-type cannot be used with agent_runtime deployment target.
Agent Runtime handles session management internally.
```
Bom — o padrão já faz o que queremos.

---

## 7. `app/__init__.py` re-exportava `agent.app` (pegadinha herdada do scaffold)

O scaffold do `agents-cli` gera:
```python
# app/__init__.py
from .agent import app
__all__ = ["app"]
```

Esse re-export faz com que qualquer `import app` (ou `from app import
discovery`) dispare o import de `app.agent` — que em alguns casos pode
falhar se env vars necessários não estiverem setados.

**Decisão**: zerei o conteúdo de `app/__init__.py`. Não há código externo
que importa `app.app` (sempre via `app.agent`). Aplicado a partir da lição
§9 da `mcp-discovery-demo/LESSONS.md`.

---

## 8. `agents-cli run` não imprime saída de turnos que só executam código

**Observação durante validação E2E**: ao reusar uma sessão (multi-turn) e
pedir ao agente "use o df existente e mostre X", o CLI `agents-cli run`
imprimiu `[code_analyst]: ` vazio mesmo com a chamada tendo sucesso
(`HTTP 200 OK` nos logs do Reasoning Engine).

**Causa provável**: quando Gemini opta por retornar APENAS partes
`executable_code` + `code_execution_result` (sem text wrapper), o
`agents-cli run` parece filtrar apenas as partes `text` para exibir,
deixando a impressão de resposta vazia. A informação real está no
`code_execution_result` que é retornado mas não renderizado.

**Workaround para a demo**: instruir explicitamente o LLM a responder em
texto também (ex: "começe sua resposta com 'O df tem N linhas'") — força
text part. Ou usar a Console Playground (renderiza todas as partes).

**Para verificação**: Cloud Trace mostra TUDO — `execute_code` span tem o
código + stdout/stderr completos.

**Não é bloqueante** para a demo — apenas afeta a apresentação no CLI.
Documentado para que quem rodar o demo entenda que a sessão e o sandbox
realmente persistem (verificável no trace).

## 9. Atos de segurança — comportamentos sutis observados

### `pip install` (Ato 3) — `requests` JÁ está no sandbox

Esperávamos que `subprocess.run(['pip', 'install', 'requests'])` falhasse.
Na verdade retorna `returncode=0` com mensagem
"Requirement already satisfied" — porque `requests` é uma das ~40 libs
pré-instaladas. A LLM identificou e explicou a sutileza: pacote já existe;
outro pacote inexistente falharia por falta de rede.

**Para um teste de bloqueio mais limpo**, use um pacote certamente
não-instalado, ex: `subprocess.run([sys.executable, '-m', 'pip', 'install',
'fake-pkg-xyz-12345'])`.

### Limite de memória (Ato 4) — lazy allocation impede `np.zeros` óbvio

`np.zeros(50_000_000_000)` (400 GB virtuais) retorna sucesso porque o
kernel Linux usa lazy allocation — só aloca páginas físicas quando
escritas. A LLM explicou perfeitamente o overcommit.

**Para forçar erro de memória real**, preencha valores:
```python
arr = np.empty(N, dtype=float64)
arr[:] = 1.0   # força allocation de páginas físicas
```

Ou use `np.random.randn(N)` que aloca + escreve.

## 12. Gemini 3 Flash bypassa nosso sandbox via code execution NATIVA

**Sintoma observado em produção**: o usuário pediu na Playground "gere
dados sintéticos de IoT de vento em fazendas, ≥10000 linhas em CSV, use
seu sandbox" — o agente respondeu com texto inicial mas **nunca entregou
o CSV**, parando silenciosamente.

**Investigação**:
- `update_time` do nosso `code-analyst-shared-sandbox` ficou congelado
  (15:02:31 antes E depois dos repros) — nosso `AgentEngineSandboxCodeExecutor`
  **nunca foi chamado**
- Mas Gemini "lembrava" do `df_wind`: respondeu "10000 linhas, 7 colunas"
  quando perguntado — código foi executado em algum lugar

**Root cause** (lendo
`google/adk/flows/llm_flows/_code_execution.py` linha 332-337):

```python
code_str = CodeExecutionUtils.extract_code_and_truncate_content(
    response_content, code_executor.code_block_delimiters
)
if not code_str:
    return  # ← sai sem chamar nosso execute_code
```

E em `code_execution_utils.py:extract_code_and_truncate_content`:

```python
for idx, part in enumerate(content.parts):
    if part.executable_code and (
        idx == len(content.parts) - 1
        or not content.parts[idx + 1].code_execution_result
    ):
        return part.executable_code.code
```

Quando Gemini 3 Flash emite numa única response **`executable_code` +
`code_execution_result` JUNTOS** (execução nativa do Gemini, não
roteada por nós), o `extract_code_and_truncate_content` vê que existe um
`code_execution_result` logo após o `executable_code` e **assume "já foi
executado, skip"** — nosso sandbox externo é bypassed.

Para outputs grandes (10000 linhas), a execução nativa do Gemini pode:
- Estourar token limit do response (~8k tokens default)
- Entrar em AFC loop tentando refinar (max=10)
- Retornar `finish_reason=MAX_TOKENS` sem texto wrapper final → agente
  parece "parado" na UI

**Mitigação aplicada (system instruction)**: agent.py agora tem uma
"REGRA CRÍTICA" no `_INSTRUCTION` explicitamente pedindo:
- Use blocos ` ```python ``` ` markdown (não API-native code execution)
- Para datasets > 1000 linhas, salve em arquivo e mostre só `head()` +
  `shape` + caminho — não o CSV inteiro

Isto é uma mitigação **best-effort** — o modelo pode ignorar a system
instruction e usar a nativa mesmo assim. Trabalho futuro:

| Opção | Como | Custo |
|---|---|---|
| A. Subclassar Gemini model no ADK pra setar `tools=[]` (suprime native code) | Override do request building | ~30 linhas |
| B. Pre-process `llm_request` em callback pra filtrar tools | ADK callback hook | ~10 linhas, mais elegante |
| C. Usar `BuiltInCodeExecutor` deliberadamente (Gemini-native) e aceitar que perdemos controle do sandbox externo | Trocar 1 linha | Perde TTL, perde governance |
| D. Forçar via `automatic_function_calling.disable=True` na config Gemini | Requer expor essa config no ADK Agent | Não exposto pelo ADK público hoje |

Para esta demo, ficamos com A/B + system instruction; documentado no
issue tracker como follow-up.

## 11. TTL eterno do sandbox (1 ano) — fix com pre-create

**Observação**: o `AgentEngineSandboxCodeExecutor` da ADK, no caminho de
**Modo 3** (lazy-create), hardcoda `ttl='31536000s'` (1 ano) no
`agent_engines.sandboxes.create()`. Validado lendo o source (linhas 152-165
de `agent_engine_sandbox_code_executor.py`) e empiricamente — listei
nosso sandbox e `expire_time` ficou em `2027-05-19` (exatamente 1 ano após
criação).

**Consequência**: cada sessão deixa um sandbox vivo por 1 ano. Em demo é OK;
em produção é desastre de cost (sandboxes idle consumindo recursos).

**Por que não dá pra patchar pós-criação**: a SDK
(`client.agent_engines.sandboxes`) não expõe `update()` nem `patch()`.
Métodos disponíveis: `create / delete / execute_code / get / list /
send_command / generate_access_token / generate_browser_ws_headers`.
TTL é imutável após criação.

**Fix escolhido — Opção B (pre-create)**: `deploy.sh` Step 5 pré-cria UM
sandbox com `ttl=${SANDBOX_TTL}` (default 3600s = 1h) e injeta seu
resource name como `SANDBOX_RESOURCE_NAME` env var. O `agent.py` usa Modo 1
do executor (`sandbox_resource_name=...`) que reusa esse sandbox em TODAS
as sessões.

**Trade-off aceito**: state é compartilhado entre sessões (variáveis criadas
pelo usuário A ficam visíveis para o usuário B). Para esta demo:
- ✅ Lifetime controlado (1h em vez de 1 ano)
- ✅ Idempotente: se sandbox existe + RUNNING + expiry > now+5min, reusa
- ✅ Refresh automático: `execute_code` reseta TTL (cada chamada estende +1h)
- ⚠️ Sem isolamento por usuário — aceitável para demo single-tenant

**Alternativas consideradas**:

| Opção | Trade-off |
|---|---|
| A. Subclassar `AgentEngineSandboxCodeExecutor` e copiar `execute_code` com TTL custom | ~80 linhas de código duplicado; frágil contra updates da ADK |
| **B. Pre-create + Modo 1 (escolhida)** | Simples; state shared entre sessões |
| C. Cleanup job (cron) que deleta sandboxes idle | Mais complexo (Cloud Scheduler + Cloud Function); preserva isolamento |

Para produção multi-tenant: combinar B + C — pre-create por user (ID na
display_name) + cron que limpa sandboxes idle > N horas.

### Gotcha pego durante validação: agente quebra silenciosamente após TTL expirar

Com `SANDBOX_TTL=3600s` (nossa primeira tentativa), descobrimos
empiricamente que **após a TTL expirar, o `SANDBOX_RESOURCE_NAME` env var
aponta para um recurso 404 e `execute_code` falha**. O executor da ADK em
Modo 1 NÃO tem fallback de re-criação (ao contrário de Modo 3, que
re-cria se `session.state['sandbox_name']` aponta para um sandbox
não-RUNNING).

**Fix prático**: re-rodar `./deploy.sh` — é idempotente:
- Step 5 lista sandboxes existentes sob o host, não encontra match → cria novo
- Step 6 re-deploya o agente com `SANDBOX_RESOURCE_NAME=<novo>`

**Default bumpado para 24h** (em vez de 1h) para reduzir frequência de
redeploy em demos. Para CI/CD ou produção, opções:
1. Cron que roda `./deploy.sh` periodicamente (a cada N horas)
2. Subclassar `AgentEngineSandboxCodeExecutor` para fallback-recriar em 404
3. Voltar para Modo 3 + cleanup cron (limpa por update_time, não TTL)

## 10. Outras decisões menores

### Testes integration do scaffold removidos

O scaffold gera `tests/integration/test_agent.py` que testa o agente
default (weather/time). Esses testes não fazem sentido para o nosso agent
e quebrariam ao importar. Removidos.

### Sem GE registration (Rule #10 pulada)

Demo é focada em code execution; não há benefício imediato em registrar
no Gemini Enterprise. O bloco opcional pode ser adicionado depois se
necessário.

### `tool_name_prefix` não se aplica

Code execution não passa por `MCPToolset` — é uma tool built-in do ADK que
o LLM chama via `code` virtual tool. Não há prefixo a configurar.

---

## Referências cruzadas

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — guia da implementação atual
- [`README.md`](./README.md) — quickstart + troubleshooting
- [`DEMO.md`](./DEMO.md) — roteiro de demo PT-BR
- [`LEARNINGS.md` do repo root](../LEARNINGS.md) — padrões reutilizáveis
  validados em outras demos (SPIFFE, telemetry, sem `gcloud ai
  reasoning-engines`)
- `mcp-discovery-demo/LESSONS.md` — lições aprendidas no demo anterior,
  algumas das quais aplicamos aqui (§9 sobre `app/__init__.py`, §8 sobre
  introspecção do `agents-cli deploy`)
