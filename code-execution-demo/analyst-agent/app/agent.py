"""Code Analyst — ADK agent with Agent Engine sandbox code execution.

Architecture:
  * The agent uses Gemini to plan, then generates Python code that runs in
    an Agent Engine sandbox via AgentEngineSandboxCodeExecutor. State
    (Python variables, DataFrames, plots) persists across turns within a
    single VertexAI session.
  * The sandbox is hosted under a DEDICATED Reasoning Engine — the
    "sandbox host" — pre-created by deploy.sh Step 4. Its full resource
    name (`projects/{P}/locations/{L}/reasoningEngines/{ID}`) is injected
    via env var AGENT_ENGINE_RESOURCE_NAME.
  * If the env var is unset, the executor falls back to auto-creating an
    Agent Engine on first execute_code() call (Case 2 in the executor
    source). We avoid that path in production deploys to prevent
    proliferation of orphan Reasoning Engines.

See ARCHITECTURE.md §2 for the full sandbox model and IAM requirements.
"""
from __future__ import annotations

import logging
import os

import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor

logger = logging.getLogger(__name__)

_, _project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", _project_id or "")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

_AGENT_ENGINE = os.environ.get("AGENT_ENGINE_RESOURCE_NAME", "").strip() or None
if _AGENT_ENGINE:
    logger.info("Sandbox host (Agent Engine): %s", _AGENT_ENGINE)
else:
    logger.warning(
        "AGENT_ENGINE_RESOURCE_NAME not set — executor will auto-create a "
        "new Agent Engine on first code execution (not recommended for prod)"
    )


_INSTRUCTION = """\
Você é o **code_analyst**, um analista de dados que escreve e executa código
Python sob demanda num sandbox seguro do Agent Engine.

## Capacidades do sandbox

- Python 3 + bibliotecas pré-instaladas: `pandas`, `numpy`, `matplotlib`,
  `scipy`, `sklearn`, `plotly`, `statsmodels`, `sympy`, e outras ~40.
- **Estado persiste entre turnos** da conversa — variáveis criadas num turno
  (ex: `df = pd.DataFrame(...)`) sobrevivem ao próximo, na mesma sessão.
- **Sem rede**: `import urllib`, `requests.get(...)`, `socket` — bloqueados.
  Você não consegue baixar dados externos.
- **Sem instalação de pacotes**: `pip install`, `subprocess` para shells —
  bloqueados. A superfície de bibliotecas é fixa.
- **Limites de recursos**: cada execução tem timeout e limite de memória. Se
  atingir, a execução é cortada e você recebe stderr.

## Como você trabalha

1. Para perguntas analíticas, escreva código Python **claro e idiomático**,
   com `print()` para mostrar resultados intermediários ao usuário.
2. Quando o usuário pede dados, **gere sinteticamente** (use `np.random.seed`
   para reprodutibilidade) ou referencie um DataFrame já criado num turno
   anterior.
3. Para visualizações, use `matplotlib` (apenas `plt.show()` — o sandbox
   captura a figura como PNG e a retorna ao usuário automaticamente).
4. Antes de operações pesadas, considere o tamanho do dataset. Se for grande,
   vetorize via numpy/pandas em vez de loops Python.
5. Sempre que reaproveitar estado de turnos anteriores, mencione (ex:
   "usando o `df` criado anteriormente").

## Idioma
Responda sempre em **português brasileiro**. Mantenha código Python e termos
técnicos (DataFrame, ticker, PnL) em inglês.

## Política em caso de erro
- Se uma operação for bloqueada por segurança (ex: tentativa de import urllib
  para fazer request HTTP), **explique honestamente ao usuário** que o
  sandbox não permite rede — não finja que não tentou.
- Se atingir timeout ou erro de memória, sugira otimização (vetorizar,
  reduzir N, usar tipos menores).
- Se um pacote não estiver disponível (`ModuleNotFoundError`), informe que a
  superfície de bibliotecas é fixa e sugira alternativa equivalente.
"""

root_agent = Agent(
    name="code_analyst",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    instruction=_INSTRUCTION,
    code_executor=AgentEngineSandboxCodeExecutor(
        agent_engine_resource_name=_AGENT_ENGINE,
    ),
)

app = App(root_agent=root_agent, name="app")
