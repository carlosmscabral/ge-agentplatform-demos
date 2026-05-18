# code-execution-demo — Arquitetura

Guia técnico do **Data Analyst com Agent Engine Sandbox**: um agente ADK
rodando no Agent Runtime com identidade SPIFFE que gera e executa código
Python dinamicamente num **sandbox isolado gerenciado pelo Agent Engine**,
mantendo estado entre turnos da conversa.

Este documento descreve a implementação atual. Para o histórico de
decisões, alternativas consideradas e bugs encontrados, veja
[`LESSONS.md`](./LESSONS.md).

---

## 1. Visão geral

```
                ┌──────────────────────────────────────────────────┐
                │            Agent Runtime (Vertex AI)             │
                │                                                  │
                │   ┌───────────────────────────────────────────┐  │
                │   │  code-analyst (ADK + SPIFFE identity)     │  │
   usuário ───►│   │                                           │  │
                │   │  - Gemini gera código Python              │  │
                │   │  - executor: AgentEngineSandboxCode-      │  │
                │   │    Executor(agent_engine_resource_name=…) │  │
                │   │  - session.state['sandbox_name'] cacheado │  │
                │   └─────────────────┬─────────────────────────┘  │
                └─────────────────────┼────────────────────────────┘
                                      │  agent_engines.sandboxes.execute_code(name=…)
                                      ▼
                ┌──────────────────────────────────────────────────┐
                │  Sandbox-host Reasoning Engine (pré-criado)      │
                │  display_name: code-analyst-sandbox-host         │
                │                                                  │
                │  ┌────────────────────────────────────────────┐  │
                │  │  Sandbox por sessão (criado sob demanda)   │  │
                │  │                                            │  │
                │  │  Python 3 + pandas + numpy + matplotlib +  │  │
                │  │  scipy + sklearn + plotly + statsmodels +  │  │
                │  │  sympy + ~30 outras                        │  │
                │  │                                            │  │
                │  │  ❌ sem rede                                │  │
                │  │  ❌ sem pip install                         │  │
                │  │  ⏱ timeout + memória limitados              │  │
                │  │  💾 estado persiste por sessão              │  │
                │  └────────────────────────────────────────────┘  │
                └──────────────────────────────────────────────────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────────────┐
                │  Cloud Trace + Logs (audit trail completo)       │
                │  - spans: generate_content, execute_code, …      │
                │  - payloads: código gerado + stdout/stderr       │
                └──────────────────────────────────────────────────┘
```

**Ideia central**: o agente vê uma "ferramenta" implícita de execução de
código. O LLM gera Python; ADK roteia para o sandbox; resultado volta como
mensagem na conversa. O sandbox vive numa Reasoning Engine separada
(o "sandbox host") para evitar proliferação de recursos.

---

## 2. O modelo do `AgentEngineSandboxCodeExecutor`

### 2.1 API e wiring

```python
# analyst-agent/app/agent.py
from google.adk.code_executors import AgentEngineSandboxCodeExecutor

_AGENT_ENGINE = os.environ.get("AGENT_ENGINE_RESOURCE_NAME", "").strip() or None

root_agent = Agent(
    name="code_analyst",
    model="gemini-3-flash-preview",
    instruction=_INSTRUCTION,
    code_executor=AgentEngineSandboxCodeExecutor(
        agent_engine_resource_name=_AGENT_ENGINE,  # apontando para sandbox-host
    ),
)
```

O construtor aceita 3 modos (validado lendo
`code_executors/agent_engine_sandbox_code_executor.py` linhas 53-103):

| Modo | Args | Comportamento |
|---|---|---|
| 1 | `sandbox_resource_name="projects/.../sandboxEnvironments/789"` | Usa sandbox pré-existente |
| 2 | (nada) | **Auto-cria** um Agent Engine novo na primeira `execute_code` (line 122: `agent_engines.create()`) |
| 3 | `agent_engine_resource_name="projects/.../reasoningEngines/456"` | **Cria sandboxes embaixo deste RE** |

**Escolhemos modo 3** com pre-create do RE host em `deploy.sh` (Step 4).
Por que NÃO modo 2:
- Cada réplica do orquestrador criaria SEU PRÓPRIO Agent Engine na primeira
  invocação → proliferação de REs órfãos, custo extra, console confusa.
- Modo 3 com pré-create concentra todos os sandboxes embaixo de UM host.

### 2.2 Ciclo de vida do sandbox (por sessão)

```
session.state["sandbox_name"] = None
                │
                ▼ primeira chamada execute_code()
                │
        agent_engines.sandboxes.create(
            spec={"code_execution_environment": {}},
            name=<sandbox_host_RE>,
            ttl="31536000s",  # 1 ano
        )
                │
                ▼
        session.state["sandbox_name"] = "projects/.../sandboxEnvironments/789"
                │
                ▼ próximas execute_code() na MESMA sessão
                │
        agent_engines.sandboxes.get(name=<sandbox_name>)
                │
        STATE_RUNNING? ─── não ──► recria sandbox (estado perdido)
                │
                ▼ sim
                │
        agent_engines.sandboxes.execute_code(name=<sandbox_name>, input_data={
            "code": "df = pd.DataFrame(...)",
            "files": []
        })
                │
                ▼
        retorna stdout, stderr, output_files (PNG dos plots, etc.)
```

**Implicações**:
- Variáveis Python (ex: `df`) persistem entre turnos da MESMA sessão
- Sessão nova = sandbox novo = estado zerado
- TTL do sandbox = 1 ano; estado interno perde-se após 14 dias ociosos
- Cold start do sandbox: ~1-2s adicionais no primeiro `execute_code`

### 2.3 Sessões persistentes — `VertexAiSessionService`

O scaffold `agents-cli scaffold create ... --deployment-target agent_runtime`
configura o `AgentEngineApp` (vide `app/agent_runtime_app.py`) usando a
session service padrão do Agent Runtime — que **persiste session state**
automaticamente em Agent Platform Sessions backend.

Implicação prática:
```bash
# Turn 1 — cria df e retorna session-id
agents-cli run --url "${ORCH_URL}" --mode adk "Crie df com 1000 vendas"
# → Session: 12345

# Turn 2 — reusa o mesmo sandbox, df ainda existe
agents-cli run --url "${ORCH_URL}" --mode adk --session-id 12345 \
    "Plote histograma"
```

Sem `--session-id`, cada `agents-cli run` cria sessão nova = sandbox novo.

---

## 3. Fluxo end-to-end

```
usuário ──► "Crie um DataFrame com 1000 vendas sintéticas (seed=42)"
              │
              ▼
        ┌────────────────────────────────────────────────────┐
        │ Gemini decide: preciso executar código Python      │
        │ (built-in tool "code" auto-disponível pelo ADK     │
        │  porque o agent tem code_executor)                 │
        └─────────────────────┬──────────────────────────────┘
                              │
                              ▼
        Gemini gera:
            import pandas as pd
            import numpy as np
            np.random.seed(42)
            df = pd.DataFrame({
                'regiao': np.random.choice(['SP','RJ','MG','RS'], 1000),
                'produto': np.random.choice(['A','B','C'], 1000),
                'valor': np.random.gamma(2.0, 50.0, 1000),
            })
            print(df.describe())
                              │
                              ▼
        ADK → AgentEngineSandboxCodeExecutor.execute_code(code)
                              │
                              ▼
        ┌────────────────────────────────────────────────────┐
        │  Primeira invocação (cache miss):                  │
        │   1. session.state['sandbox_name'] não existe      │
        │   2. POST .../reasoningEngines/<host>:create       │
        │      sandbox (TTL 1 ano)                           │
        │   3. cacheia sandbox_name em session.state         │
        │   4. POST .../sandboxes/<sandbox>:executeCode      │
        │   5. retorna stdout, stderr, output_files          │
        │                                                    │
        │  Próximas invocações na mesma sessão:              │
        │   1. session.state['sandbox_name'] cached          │
        │   2. GET sandbox → STATE_RUNNING? ✓                │
        │   3. POST .../sandboxes/<sandbox>:executeCode      │
        │   4. df ainda na memória do interpretador          │
        └─────────────────────┬──────────────────────────────┘
                              │
                              ▼
        stdout → Gemini → resposta ao usuário em português
                              │
                              ▼
        usuário ◄── "Criei o DataFrame `df` com 1000 vendas..."
```

Latência típica:

| Cenário | Tempo |
|---|---|
| Primeiro turno (cold sandbox) | 5-8s (LLM + create sandbox + execute) |
| Turnos subsequentes (warm sandbox) | 2-4s (LLM + execute) |
| Execução pura (sem LLM, hypothetical) | 0.5-1s |

---

## 4. Bibliotecas pré-instaladas no sandbox

Inventory parcial (validado empiricamente):

| Categoria | Libs |
|---|---|
| Data | `pandas`, `numpy`, `pyarrow` |
| Plotting | `matplotlib`, `plotly`, `seaborn` |
| Stats / ML | `scipy`, `scikit-learn`, `statsmodels`, `xgboost` |
| Symbolic / opt | `sympy`, `cvxpy` |
| Utilities | `json`, `csv`, `re`, `datetime`, `collections` (stdlib) |

O LLM tipicamente sabe quais libs estão disponíveis pelo conhecimento de
mundo. Se errar (`ModuleNotFoundError`), a instrução do agente o orienta a
sugerir alternativa equivalente da mesma família.

**O que NÃO está disponível** (security):
- `urllib`, `requests`, `httpx`, `socket` — nenhuma chamada de rede
- `subprocess`, `os.system` para shells externos — sem package install
- Acesso a credenciais GCP / Application Default Credentials
- Filesystem do host do orquestrador

---

## 5. Segurança e boundaries

| Aspecto | Como é enforced | O que o LLM/código pode | O que NÃO pode |
|---|---|---|---|
| **Rede** | Sandbox sem egress | Computar com dados in-memory | Fazer HTTP, DNS, socket |
| **Filesystem** | Sandbox isolado | Arquivos temporários intra-sandbox; output_files (PNGs) | Ler /etc, /home, montar volumes |
| **Pacotes** | Conjunto fixo de libs | Importar qualquer lib pré-instalada | `pip install`, modificar PYTHONPATH |
| **Recursos** | Timeout + memória limitados pela platform | Cálculos modestos (até ~1GB RAM) | Loops infinitos, allocations gigantes |
| **Identidade GCP** | Sandbox roda sob SA do Agent Engine, NÃO usa SPIFFE do agente | Operações no escopo do projeto-sandbox | Chamadas como o orquestrador SPIFFE |
| **Audit** | Cada bloco em span do Cloud Trace via OTEL | — | (não há "off the record" — todo código + stdout/stderr é logado) |

### Audit trail no Cloud Trace

Habilitado por `deploy.sh` Step 5 via env vars:
```
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY
OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
```

Spans esperados num trace típico:

```
code_analyst (root span)
├── generate_content              [Gemini: gera o código]
│   └── attrs: model=gemini-3-flash-preview, code=<o código gerado>
├── execute_code                  [AgentEngineSandbox span]
│   ├── attrs: code=<o código>, sandbox_name=<resource path>
│   ├── http: POST .../sandboxes/<id>:executeCode
│   └── return: stdout=<…>, stderr=<…>, output_files=[…]
└── generate_content              [Gemini: compõe resposta final]
```

Significa que para compliance, **todo código executado + sua saída** está
disponível para auditoria via Cloud Trace + Cloud Logging.

---

## 6. Identidade e modelo IAM

### Identidade do orquestrador

`deploy.sh` Step 5 deploya com `agents-cli deploy --agent-identity`:
- Agent Runtime emite cert SPIFFE (X.509, 24h, auto-rotacionado)
- Principal final: `principal://agents.global.…/resources/aiplatform/…/reasoningEngines/<orchestrator_id>`

### IAM concedido (Step 2 do deploy.sh)

Tabela completa de bindings ao **principalSet** SPIFFE do projeto:

| Role | Para quê |
|---|---|
| `roles/aiplatform.agentDefaultAccess` | Baseline agente |
| `roles/aiplatform.user` | **Crítica**: cobre `agent_engines.sandboxes.*` no sandbox-host RE |
| `roles/serviceusage.serviceUsageConsumer` | Quota do projeto |
| `roles/logging.logWriter` | Escrever logs |
| `roles/monitoring.metricWriter` | Emitir métricas |
| `roles/cloudapiregistry.viewer` | Cloud API Registry |
| `roles/storage.objectAdmin` | Bucket de staging |

**Não há IAM per-RE**: como o `aiplatform.user` é concedido a nível de
projeto, o orquestrador SPIFFE automaticamente tem acesso a chamar
`agent_engines.sandboxes.create/execute_code` em qualquer reasoning engine
do projeto — incluindo o sandbox-host. Per-RE IAM seria redundante.

### Identidade dentro do sandbox

O código que roda no sandbox **não tem** as credenciais SPIFFE do
orquestrador. O sandbox roda sob um SA gerenciado pelo Agent Engine, com
escopo mínimo. Mesmo se o código tentasse `google.auth.default()`, falharia
ou retornaria credenciais inertes (sem acesso a APIs).

### O que quebra se você pular algum passo

| Se faltar… | Sintoma |
|---|---|
| `aiplatform.user` no principalSet | `PermissionDenied` ao tentar criar sandbox |
| `--agent-identity` no deploy | Agente roda com SA padrão do RE, principalSet não aplica → mesma falha |
| `AGENT_ENGINE_RESOURCE_NAME` env var | Executor entra no modo 2 (auto-create) e cria um RE novo a cada invocação → proliferação |
| Pre-create do sandbox-host RE | (mesma coisa que acima — recurso aparece como side-effect, sem nome controlado) |

---

## 7. Loop de desenvolvimento local-first

```
Local: importar o módulo sem env vars
       │
       ▼ não dá erro (executor entra em modo "auto-create" silenciosamente)
Local: smoke do agente (precisa ADC + sandbox-host RE existente)
       │
       ▼ verde
./deploy.sh  ← só agora custa cloud
       │
       ▼ verde
agents-cli run --url <orch> --mode adk "..."
       │
       ▼ rastros validados no Cloud Trace
done
```

Não temos unit tests do executor (mockar `AgentEngineSandboxCodeExecutor`
exigiria recriar todo o protocolo de sandbox). O scaffold removeu os
integration tests automáticos (que testavam um agente weather/time que não
existe mais aqui).

---

## 8. Verificação no Cloud Trace

Filtrar por `service.name="code_analyst"` no Cloud Trace. Spans esperados
para uma pergunta típica:

```
code_analyst (root, 5-8s)
├── generate_content                              ~1-2s
│   └── code=<código Python gerado>
├── execute_code                                  ~2-5s
│   ├── code=<mesmo código>
│   ├── sandbox_name=projects/.../sandboxEnvironments/<id>
│   └── http: POST .../sandboxes/<id>:executeCode
└── generate_content                              ~1-2s (composição)
```

A cobertura de payload está habilitada via OTEL env vars (Step 5 do deploy),
então o código gerado e a saída ficam visíveis nos spans para auditoria.

---

## Referências cruzadas

- [`README.md`](./README.md) — quickstart, prerequisitos, troubleshooting
- [`DEMO.md`](./DEMO.md) — roteiro PT-BR de 5 atos
- [`LESSONS.md`](./LESSONS.md) — histórico de decisões e bugs
- [`LEARNINGS.md` do repo root](../LEARNINGS.md) — padrões reutilizáveis em
  outras demos (SPIFFE, telemetry, IAM)
- ADK source: `analyst-agent/.venv/lib/python3.12/site-packages/google/adk/code_executors/agent_engine_sandbox_code_executor.py`
