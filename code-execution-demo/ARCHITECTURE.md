# code-execution-demo — Arquitetura

Guia técnico do **Data Analyst com Gemini API code execution**: um agente
ADK rodando no Agent Runtime com identidade SPIFFE que gera e executa
código Python dinamicamente num sandbox gerenciado pela **Gemini API**.

## ⚠️ Disambiguação importante — qual "code execution"?

Existem **DOIS produtos distintos** no GCP com nomes parecidos:

| | **Gemini API Code Execution** ← USAMOS ESSE | Agent Engine Code Execution Sandbox |
|---|---|---|
| Doc | [vertex-ai/generative-ai/docs/multimodal/code-execution](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/code-execution) | [gemini-enterprise-agent-platform/scale/sandbox/code-execution-overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/sandbox/code-execution-overview) |
| Invocação | `Tool(code_execution=ToolCodeExecution())` em `tools=[]` da Gemini API | `client.agent_engines.sandboxes.create/execute_code` |
| ADK wrapper | `BuiltInCodeExecutor()` | `AgentEngineSandboxCodeExecutor(...)` |
| Sandbox | "fixed, isolated environment in the API backend" (Gemini API gerencia) | gVisor exposto como recurso `sandboxEnvironments/...` |
| Provisionamento | Zero (transparente — Gemini cuida) | Manual via `agent_engines.create()` + `sandboxes.create()` |
| Lifecycle | Per-request | Configurável (TTL default 14d) |
| Console visível | Não aparece como recurso | Aparece como `sandboxEnvironments` |
| Timeout | 30s hardcoded | Configurável |
| State persistence | Dentro do AFC loop da request | Per session (até 14d) |
| Listar/inspecionar | Não há API | `sandboxes.list/get/delete` |

**Por que essa demo acabou usando o primeiro e não o segundo?** Investigação
completa em [`LESSONS.md` §12](./LESSONS.md) — TL;DR: quando registramos
`AgentEngineSandboxCodeExecutor` no ADK Agent, Gemini 2.5+ ignora e usa
sua própria code execution nativa (a do primeiro produto). ADK detecta
os `code_execution_result` parts e skip nosso external executor. Nosso
sandbox provisionado fica órfão.

Esta demo então mostra **Gemini API Code Execution wired através do ADK
num agente ADK SPIFFE-bound no Agent Runtime**. O sandbox é
Google-managed via Gemini API, não via Agent Engine resources API.

Para uso com Agent Engine sandboxes propriamente ditos (com TTL,
listagem, etc.), seria necessário um fix no ADK (subclass executor com
before_model_callback que strip `code_execution_result` parts), o que
está fora do escopo desta demo.

---

## 1. Visão geral

```
                ┌──────────────────────────────────────────────────┐
                │            Agent Runtime (Vertex AI)             │
                │                                                  │
                │   ┌───────────────────────────────────────────┐  │
                │   │  code-analyst (ADK + SPIFFE identity)     │  │
   usuário ───►│   │                                           │  │
                │   │  - Gemini gera + executa Python via      │  │
                │   │    tool nativa `code_execution`           │  │
                │   │  - executor: BuiltInCodeExecutor          │  │
                │   │    (registra a tool no llm_request)       │  │
                │   └─────────────────┬─────────────────────────┘  │
                └─────────────────────┼────────────────────────────┘
                                      │
                                      ▼ via Gemini API tool call
                ┌──────────────────────────────────────────────────┐
                │  Gemini-managed gVisor sandbox (per request)     │
                │                                                  │
                │  ┌────────────────────────────────────────────┐  │
                │  │  Python 3 + ~40 libs                       │  │
                │  │  (pandas, numpy, matplotlib, scipy, ...)   │  │
                │  │                                            │  │
                │  │  ❌ sem rede                                │  │
                │  │  ❌ sem pip install                         │  │
                │  │  ⏱ timeout + memória limitados              │  │
                │  │  💾 estado persiste dentro do AFC loop      │  │
                │  └────────────────────────────────────────────┘  │
                └──────────────────────────────────────────────────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────────────┐
                │  Cloud Trace + Logs (audit trail completo)       │
                │  - spans: generate_content (contém executable_   │
                │    code + code_execution_result)                 │
                └──────────────────────────────────────────────────┘
```

**Idéia central**: o `BuiltInCodeExecutor` registra a tool
`code_execution` no request do Gemini. Gemini então executa código
Python no seu próprio sandbox gVisor — mesma postura de segurança que o
sandbox do Agent Engine, mas com lifecycle 100% gerenciado pela Gemini API.

---

## 2. Wiring do `BuiltInCodeExecutor`

### 2.1 Código (analyst-agent/app/agent.py)

```python
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.code_executors import BuiltInCodeExecutor

root_agent = Agent(
    name="code_analyst",
    model="gemini-2.5-flash",
    instruction=_INSTRUCTION,  # ver §2.3 abaixo
    code_executor=BuiltInCodeExecutor(),
)

app = App(root_agent=root_agent, name="app")
```

### 2.2 O que `BuiltInCodeExecutor` faz internamente

Lendo o source ADK
(`google/adk/code_executors/built_in_code_executor.py`):

```python
class BuiltInCodeExecutor(BaseCodeExecutor):
    def process_llm_request(self, llm_request):
        if is_gemini_2_or_above(llm_request.model):
            llm_request.config = llm_request.config or types.GenerateContentConfig()
            llm_request.config.tools = llm_request.config.tools or []
            llm_request.config.tools.append(
                types.Tool(code_execution=types.ToolCodeExecution())
            )
```

Resultado: ADK adiciona `tools=[Tool(code_execution=ToolCodeExecution())]`
na request do Gemini. Gemini sabe que tem code execution disponível e usa
quando relevante para a pergunta. O sandbox é provisionado por Gemini,
não por nós.

### 2.3 System instruction

Modelada a partir do [tutorial oficial ADK + Agent Engine](
https://github.com/GoogleCloudPlatform/generative-ai/blob/main/agents/agent_engine/tutorial_get_started_with_code_execution.ipynb).

Pontos críticos da instrução:
- Emite código em ` ```python ``` ` ou ` ```tool_code ``` ` blocks
- Não simula resultados nem "prevê" output
- Statefulness: variáveis persistem entre execuções (no loop AFC)
- Para datasets > 1000 linhas: salva em arquivo, mostra head() + shape

---

## 3. Fluxo end-to-end

```
usuário ──► "Calcule juros compostos de $1000 a 5% por 10 anos"
              │
              ▼
        ┌────────────────────────────────────────────────────┐
        │ ADK monta llm_request com tools=[code_execution]   │
        │ (via BuiltInCodeExecutor.process_llm_request)      │
        └─────────────────────┬──────────────────────────────┘
                              │
                              ▼
        Gemini 2.5 Flash decide usar code execution:
          - Gera `executable_code` part: "amount = 1000*(1.05)**10"
          - Internamente executa no Gemini gVisor sandbox
          - Gera `code_execution_result` part: stdout/stderr
          - Gera text part: "O valor final é $1628.89"
                              │
                              ▼
        ADK retorna parts ao Runner → CLI/Playground renderiza
                              │
                              ▼
        usuário ◄── resposta com código + resultado + texto
```

Latência típica (Gemini 2.5 Flash):

| Cenário | Tempo |
|---|---|
| Cálculo simples | 2-5s |
| Geração de dataset 10k linhas + CSV save | 5-10s |
| Multi-turn com estado persistente | 3-6s por turn |

---

## 4. Bibliotecas pré-instaladas no sandbox

Inventário oficial (Gemini Code Execution overview):

| Categoria | Libs |
|---|---|
| Data | `pandas`, `numpy`, `pyarrow`, `openpyxl`, `xlrd`, `XlsxWriter` |
| Plotting | `matplotlib`, `plotly`, `seaborn` |
| Stats / ML | `scipy`, `scikit-learn`, `statsmodels`, `tensorflow` |
| Symbolic / opt | `sympy`, `mpmath`, `ortools` |
| Image / PDF | `pillow`, `opencv-python`, `imageio`, `PyPDF2`, `pylatex`, `fpdf`, `reportlab` |
| Docs | `python-docx`, `python-pptx`, `striprtf`, `lxml` |
| Geo | `geopandas`, `contourpy` |
| Misc | `chess`, `tabulate`, `joblib`, `jsonschema`, `attrs`, `protobuf` |

Lista completa em
[Code Execution Overview](
https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/sandbox/code-execution-overview).

---

## 5. Segurança e boundaries

| Aspecto | Como é enforced | O que pode | O que NÃO pode |
|---|---|---|---|
| **Rede** | Sandbox sem egress | Computar in-memory | HTTP, DNS, socket |
| **Filesystem** | Sandbox isolado | Arquivos temporários em `/tmp` | Ler `/etc`, `/home`, montar volumes |
| **Pacotes** | Conjunto fixo de libs | Importar libs pré-instaladas | `pip install`, modificar `PYTHONPATH` |
| **Recursos** | Timeout + memória limitados | Cálculos modestos | Loops infinitos, allocations gigantes |
| **Identidade GCP** | Sandbox roda sob SA do Gemini API | Operações no escopo Gemini | Chamadas como o SPIFFE do agente |
| **Audit** | Spans `generate_content` no Cloud Trace contêm `executable_code` + `code_execution_result` parts | — | Sem "off the record" — todo código + saída é logado |

### Audit trail no Cloud Trace

Habilitado por `deploy.sh` Step 4 via env vars:
```
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY
OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
```

Spans esperados num trace típico:

```
code_analyst (root span)
└── generate_content (Gemini)
    ├── code: <código Python gerado>
    ├── code_execution_result: <stdout, stderr>
    └── text: <resposta para o usuário>
```

Para compliance: todo código + sua saída fica visível no span para auditoria.

---

## 6. Identidade e modelo IAM

### Identidade do orquestrador

`deploy.sh` Step 4 deploya com `agents-cli deploy --agent-identity`:
- Agent Runtime emite cert SPIFFE (X.509, 24h, auto-rotacionado)
- Principal final: `principal://agents.global.…/resources/aiplatform/…/reasoningEngines/<orchestrator_id>`

### IAM concedido (Step 2 do deploy.sh)

Tabela completa de bindings ao **principalSet** SPIFFE do projeto:

| Role | Para quê |
|---|---|
| `roles/aiplatform.agentDefaultAccess` | Baseline agente |
| `roles/aiplatform.user` | Inferência + chamadas a Gemini API |
| `roles/serviceusage.serviceUsageConsumer` | Quota do projeto |
| `roles/logging.logWriter` | Escrever logs |
| `roles/monitoring.metricWriter` | Emitir métricas |
| `roles/cloudapiregistry.viewer` | Cloud API Registry |
| `roles/storage.objectAdmin` | Bucket de staging |

### Identidade dentro do sandbox

O código que roda no sandbox **não tem** as credenciais SPIFFE do
orquestrador. O sandbox roda sob um SA gerenciado pelo Gemini API, com
escopo mínimo. Tentativas de `google.auth.default()` retornam credenciais
inertes sem acesso a APIs.

### O que quebra se você pular algum passo

| Se faltar… | Sintoma |
|---|---|
| `aiplatform.user` no principalSet | `PermissionDenied` ao chamar Gemini |
| `--agent-identity` no deploy | Agente roda com SA padrão do RE, principalSet não aplica |

---

## 7. Loop de desenvolvimento local-first

```
Local: importar o módulo (sem env vars precisas)
       │
       ▼
Local: smoke do agente (precisa ADC)
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

Não temos unit tests do executor (Gemini built-in não é mockável
facilmente). A garantia vem do Cloud Trace pós-deploy.

---

## 8. Verificação no Cloud Trace

Filtrar por `service.name="code_analyst"` no Cloud Trace. Spans esperados
para uma pergunta típica:

```
code_analyst (root, 2-5s)
└── generate_content   ~1-3s
    ├── executable_code: <Python>
    ├── code_execution_result: <stdout>
    └── text: <resposta final>
```

A cobertura de payload está habilitada via OTEL env vars (Step 4 do
deploy), então o código gerado e a saída ficam visíveis nos spans.

---

## Referências cruzadas

- [`README.md`](./README.md) — quickstart, prerequisitos, troubleshooting
- [`DEMO.md`](./DEMO.md) — roteiro PT-BR de 5 atos
- [`LESSONS.md`](./LESSONS.md) — histórico de decisões (incluindo §12 — a
  investigação que pivotou de AgentEngineSandbox para BuiltIn)
- [Tutorial oficial ADK + Agent Engine Code Execution](
  https://github.com/GoogleCloudPlatform/generative-ai/blob/main/agents/agent_engine/tutorial_get_started_with_code_execution.ipynb)
- ADK source: `analyst-agent/.venv/lib/python3.12/site-packages/google/adk/code_executors/built_in_code_executor.py`
