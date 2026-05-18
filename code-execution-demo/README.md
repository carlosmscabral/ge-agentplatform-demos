# code-execution-demo — Data Analyst com Agent Engine Sandbox

Um agente ADK em Python deployado no Agent Runtime com identidade SPIFFE
que **gera e executa código Python sob demanda** num **sandbox isolado
gerenciado pelo Agent Engine** (`AgentEngineSandboxCodeExecutor`).

**Caso de uso (mockado)**: data analyst que cria datasets sintéticos,
calcula estatísticas e gera gráficos via código Python — mantendo estado
(variáveis, DataFrames) entre turnos da conversa, no mesmo sandbox.

## O que isso demonstra

| Capability | Como |
|---|---|
| Code execution dinâmica + segura | `AgentEngineSandboxCodeExecutor` — sandbox sem rede, sem instalação de pacotes |
| Estado por sessão | Variáveis Python (ex: `df`) sobrevivem entre turnos do mesmo `session_id` |
| Bibliotecas de data science | pandas, numpy, matplotlib, scipy, sklearn, plotly, statsmodels, sympy (~40 libs) |
| SPIFFE identity para o agente | `agents-cli deploy --agent-identity` + grants em `principalSet` |
| Audit trail | Cada bloco de código + stdout/stderr fica em span do Cloud Trace |

## Quick start

```bash
cd code-execution-demo
cp .env.template .env             # ajuste se quiser nomes/região diferentes
./deploy.sh                       # ~10-15 minutos (cria sandbox-host RE + orquestrador)

# Teste multi-turn
cd analyst-agent
agents-cli run --url "<ORCH_URL>" --mode adk \
    "Crie um DataFrame com 1000 vendas sintéticas (seed=42) e mostre .describe()"

# Resume a mesma sessão para reusar o df criado
agents-cli run --url "<ORCH_URL>" --mode adk --session-id "<id>" \
    "Plote um histograma dos valores"
```

Tear down:

```bash
./undeploy.sh
```

## Prerequisites

### Tooling
- GCP project com billing
- `gcloud` autenticado (`gcloud auth login` + `gcloud auth application-default login`)
- `uv` instalado
- `agents-cli` instalado: `uv tool install google-agents-cli`

### APIs necessárias
```bash
gcloud services enable \
    aiplatform.googleapis.com \
    cloudbuild.googleapis.com \
    iamcredentials.googleapis.com \
    logging.googleapis.com \
    monitoring.googleapis.com
```

### IAM (aplicado automaticamente por `deploy.sh` Step 2 ao SPIFFE principal set do projeto)

| Role | Para quê |
|---|---|
| `roles/aiplatform.agentDefaultAccess` | Capacidades baseline do agente |
| `roles/aiplatform.user` | Inferência + chamadas a `agent_engines.sandboxes.*` |
| `roles/serviceusage.serviceUsageConsumer` | Quota do projeto |
| `roles/logging.logWriter`, `roles/monitoring.metricWriter` | Observabilidade |
| `roles/cloudapiregistry.viewer` | Cloud API Registry |
| `roles/storage.objectAdmin` | Bucket de staging |

E `deploy.sh` Step 7 concede explicitamente ao SPIFFE principal **específico
do orquestrador** o role `roles/aiplatform.user` no Reasoning Engine "sandbox
host" — necessário para chamar `agent_engines.sandboxes.create/execute_code`.

## Configuração (`.env`)

| Variável | Default | Para quê |
|---|---|---|
| `PROJECT_ID` | auto via gcloud | GCP project ID |
| `PROJECT_NUMBER` | auto | Número do projeto (para SPIFFE principal set) |
| `REGION` | `us-central1` | Região do Reasoning Engine |
| `STAGING_BUCKET` | `${PROJECT_ID}-code-exec-staging` | Bucket de logs + agent staging |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Modelo usado pelo agente |
| `ORCHESTRATOR_DISPLAY_NAME` | `code-analyst` | Display name na console |
| `SANDBOX_HOST_DISPLAY_NAME` | `code-analyst-sandbox-host` | Display name do RE host de sandboxes |

> `AGENT_ENGINE_RESOURCE_NAME` **não** vai no `.env` — é descoberto/criado
> por `deploy.sh` Step 4 e injetado nas env vars do runtime via
> `--update-env-vars`. Veja [`ARCHITECTURE.md` §2](./ARCHITECTURE.md).

## O que está no repo

```
code-execution-demo/
├── deploy.sh / undeploy.sh        # 9-step idempotent deploy + cleanup
├── .env.template                  # todas as env vars documentadas
├── README.md                      # este arquivo
├── ARCHITECTURE.md                # guia técnico PT-BR da implementação
├── DEMO.md                        # 5 atos PT-BR (multi-turn + 3 security + audit)
├── LESSONS.md                     # decisões e bugs encontrados
└── analyst-agent/                 # scaffold ADK customizado
    └── app/
        ├── agent.py               # Agent + AgentEngineSandboxCodeExecutor
        └── agent_runtime_app.py   # gerado pelo scaffold
```

## Troubleshooting quick reference

| Sintoma | Causa provável | Fix |
|---|---|---|
| Agente nunca executa código (resposta só texto) | Modelo não suporta tool calling para code execution | Use Gemini 2.0+ (default já é `gemini-3-flash-preview`) |
| `AGENT_ENGINE_RESOURCE_NAME not set — executor will auto-create` warning | Faltou setar a env var no deploy | Re-rode `deploy.sh` (Step 4 cria + injeta) |
| `permission denied` ao chamar `sandboxes.execute_code` | SPIFFE principal sem `aiplatform.user` no sandbox-host RE | `deploy.sh` Step 7 faz esse grant — re-rode |
| Variável `df` não persiste entre `agents-cli run`s | Cada chamada sem `--session-id` cria nova sessão = novo sandbox | Capture o `session-id` da primeira resposta e passe via `--session-id` |
| `Compute Engine Metadata server unavailable` durante deploy local | (Não aplicável aqui — executor não usa metadata server) | — |
| Sandbox-host RE não cleanup após undeploy | `undeploy.sh` precisa do `.deploy-state` | Verifique presença antes de rodar; se faltar, delete manualmente via `gcloud beta ai reasoning-engines delete <name>` |

## Further reading

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — guia em PT-BR: modelo de sandbox, IAM, fluxo end-to-end, security boundaries
- [`DEMO.md`](./DEMO.md) — roteiro PT-BR com 5 atos (multi-turn + security + audit)
- [`LESSONS.md`](./LESSONS.md) — decisões e bugs (chicken-and-egg do `agent_engine_resource_name`, etc.)
- Repo-level [`LEARNINGS.md`](../LEARNINGS.md) — padrões reutilizáveis em outras demos
- Repo-level [`CLAUDE.md`](../CLAUDE.md) — as 11 regras de produção
