# DEMO.md — code-execution-demo walkthrough

Roteiro passo-a-passo em PT-BR para demonstrar o data analyst com Agent
Engine sandbox. Total ~10 minutos após `./deploy.sh` completar.

## Prerequisites

```bash
./deploy.sh    # 10-15 min — cria sandbox-host RE + orquestrador SPIFFE
export ORCH_URL="https://us-central1-aiplatform.googleapis.com/v1beta1/projects/.../reasoningEngines/..."
cd analyst-agent
```

## ✅ Pre-flight check

Com a versão atual (Gemini API code execution), **não há sandbox
provisionado por nós** — Gemini API cuida do lifecycle. O único check
necessário é que o agente esteja UP:

```bash
ORCH_URL="<URL impressa pelo deploy.sh>"
agents-cli run --url "${ORCH_URL}" --mode adk "Olá! Responda com 'OK'."
```

Se responder, está pronto. Cold start típico do Agent Runtime: 5-15s na
primeira chamada após período idle.

> 📌 **Para demos importantes**: faça uma chamada warm-up 5 min antes
> para evitar cold start no momento da apresentação.

## FAQ — perguntas comuns durante a demo

### "Esta demo usa Agent Engine Code Execution Sandbox?"

**Não — usa Gemini API Code Execution**, que é um produto distinto.

Tentamos primeiro o `AgentEngineSandboxCodeExecutor` (= Agent Engine
sandbox, recurso `sandboxEnvironments/...` visível na console) mas
Gemini 2.5+ bypassa esse caminho. Pivotamos para `BuiltInCodeExecutor`
(= Gemini API code execution, sandbox transparente da Gemini API).
Detalhes em [`LESSONS.md` §0 e §12](./LESSONS.md).

### "Vejo um Reasoning Engine `code-analyst-sandbox-host` na console — o que é?"

Resíduo de uma iteração anterior (Agent Engine sandbox). Se ainda
aparecer, foi cleanup incompleto. A versão atual do `deploy.sh` **não
cria** mais esse recurso. Para deletar manualmente:

```bash
curl -s -X DELETE \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/<NUM>/locations/us-central1/reasoningEngines/<ID>?force=true" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)"
```

### "O agente pediu pra gerar 10000 linhas de CSV e parou de responder. O que aconteceu?"

Provavelmente o modelo entrou em loop com a code execution NATIVA do
Gemini (não o nosso sandbox) ao tentar embutir o CSV inteiro na resposta,
estourando token limits. Sintomas:

- Texto inicial "Vou gerar..." aparece, depois nada
- `update_time` do nosso sandbox não muda (sandbox externo bypassed)
- AFC max=10 → modelo pode hit limit em iterações internas

**Mitigação aplicada no agent.py**: system instruction explícita pede:
- Use markdown ` ```python ``` ` blocks (não API-native code execution)
- Para datasets > 1000 linhas, salve em `/tmp/<nome>.csv` e mostre só `head()`
  + `shape` + caminho — NÃO o CSV inteiro
- Limite I/O é 100MB/request, mas tokens de output são muito menores

Se o problema persistir: prefira prompts que peçam **resumos** ao invés de
dumps completos (ex: "salve em arquivo e me mostre só as 5 primeiras linhas").

## Access methods

| Método | Comando / link |
|---|---|
| CLI | `agents-cli run --url "${ORCH_URL}" --mode adk "<prompt>"` |
| Console Playground | `https://console.cloud.google.com/vertex-ai/agents/locations/us-central1/agent-engines/<RE_ID>/playground?project=<PROJECT_ID>` |
| Cloud Trace | `https://console.cloud.google.com/traces/list?project=<PROJECT_ID>` |

---

## Ato 1 — Análise multi-turn com estado persistente

**O que isso mostra**: o sandbox **mantém variáveis Python** entre turnos
da MESMA sessão. O LLM cria um DataFrame num turno, plota no próximo,
modifica no seguinte — sem nunca recriar os dados.

**Turn 1** (cria a sessão):
```bash
agents-cli run --url "${ORCH_URL}" --mode adk \
    "Crie um DataFrame com 1000 vendas sintéticas (regiao em SP/RJ/MG/RS, produto em A/B/C, valor seguindo gamma(2, 50), data uniformemente distribuída em 2026). Use np.random.seed(42). Mostre .describe()."
```

Captura o `Session: <id>` retornado pelo comando.

**Turn 2** (reusa a mesma sessão = mesmo sandbox):
```bash
SESSION_ID="<id-do-turn-1>"
agents-cli run --url "${ORCH_URL}" --mode adk --session-id "${SESSION_ID}" \
    "Agora plote um histograma dos valores com matplotlib. Use 30 bins."
```

**Turn 3** (modifica o df):
```bash
agents-cli run --url "${ORCH_URL}" --mode adk --session-id "${SESSION_ID}" \
    "Adicione uma coluna 'taxa' = valor * 0.15. Mostre a soma de taxa agrupada por região."
```

**O que observar**:
1. Turn 1 cria `df` no sandbox (cold sandbox: ~5-8s)
2. Turn 2 retorna PNG do histograma sem recriar o df — variável persiste
3. Turn 3 modifica o `df` (adiciona coluna) e agrupa — mesma sessão Python

**Validação no Cloud Trace**:
- 3 spans `code_analyst` (um por turn)
- Cada um com `execute_code` span apontando para `sandbox_name` IGUAL
- Código gerado pelo LLM visível nos spans (campo `code`)

---

## Ato 2 — Segurança: bloqueio de rede

**O que isso mostra**: o sandbox **não tem egress de rede**. Qualquer
tentativa de fazer request HTTP, DNS, etc. falha de forma honesta. O agente
reporta o bloqueio explicitamente em vez de fingir.

**Prompt**:
```bash
agents-cli run --url "${ORCH_URL}" --mode adk \
    "Tente baixar o conteúdo da página https://example.com usando urllib.request.urlopen e mostre os primeiros 200 caracteres. Se falhar, me diga exatamente por quê."
```

**O que observar**:
1. O LLM gera código com `urllib.request.urlopen("https://example.com")`
2. Sandbox executa e retorna stderr com algo como
   `URLError: <urlopen error [Errno -3] Temporary failure in name resolution>`
   ou `gaierror: [Errno -2] Name or service not known`
3. O agente responde em PT-BR explicando que o sandbox não tem rede,
   mencionando o erro específico

**Lição enterprise**: o código que roda no sandbox **não pode exfiltrar
dados** via HTTP — mesmo que o LLM seja persuadido a tentar.

---

## Ato 3 — Segurança: bloqueio de instalação de pacotes

**O que isso mostra**: a superfície de bibliotecas é **fixa**. Tentativas
de instalar pacotes novos (via pip, conda, apt) falham.

**Prompt**:
```bash
agents-cli run --url "${ORCH_URL}" --mode adk \
    "Tente instalar o pacote 'requests' usando subprocess para chamar pip. Reporte exatamente o que aconteceu."
```

**O que observar**:
1. O LLM gera código como `subprocess.run(['pip', 'install', 'requests'])`
   ou `subprocess.run([sys.executable, '-m', 'pip', 'install', 'requests'])`
2. Resultado: `CalledProcessError` ou retorno não-zero
   (pip não disponível, ou rede bloqueada, ou subprocess restringido)
3. Agente reporta o bloqueio honestamente

**Lição enterprise**: surface area auditável é estática. Não há injection
de supply chain via `pip install` malicioso.

---

## Ato 4 — Segurança: limites de recursos

**O que isso mostra**: cada execução tem **timeout e limite de memória**.
Loops infinitos ou allocations gigantes são cortadas.

**Prompt** (memória):
```bash
agents-cli run --url "${ORCH_URL}" --mode adk \
    "Crie um array NumPy de 100 bilhões de floats (np.zeros) e calcule sua média. Se falhar, sugira uma alternativa razoável."
```

**O que observar**:
1. O LLM gera `np.zeros(100_000_000_000)` ou similar
2. Sandbox falha com `MemoryError` ou OOM kill
3. Agente reporta e sugere reduzir N ou usar dtype menor

**Prompt alternativo** (timeout):
```bash
agents-cli run --url "${ORCH_URL}" --mode adk \
    "Calcule o fatorial de 10000 usando um loop Python puro (sem math.factorial). Se demorar muito, simplifique a abordagem."
```

**Lição enterprise**: código malicioso ou bugado não pode derrubar o
sandbox infinitamente — há limites enforced pela plataforma.

---

## Ato 5 — Audit trail completo no Cloud Trace

**O que isso mostra**: cada bloco de código gerado pelo LLM e sua saída
(stdout/stderr) está disponível no Cloud Trace para auditoria, compliance e
debug.

**Setup**:
- Abra o Cloud Trace para o projeto:
  `https://console.cloud.google.com/traces/list?project=<PROJECT_ID>`
- Filtre por `service.name="code_analyst"` ou `name="execute_code"`

**O que observar** num trace pós-Ato 1:

```
code_analyst (root span, 5-8s)
├── generate_content (Gemini)               ~1-2s
│   └── attrs: code=<código Python gerado>
├── execute_code                            ~2-5s
│   ├── code=<mesmo código>
│   ├── sandbox_name=projects/.../sandboxEnvironments/<id>
│   ├── stdout=<saída>
│   └── stderr=<vazio se sucesso>
└── generate_content (resposta final)       ~1-2s
```

**Verificações**:
- [ ] Cada `execute_code` carrega o `code` completo (campo string)
- [ ] `stdout`/`stderr` visíveis no span
- [ ] `sandbox_name` permite correlacionar múltiplas execuções na mesma
      sessão (mesmo sandbox)
- [ ] Erros (Atos 2/3/4) aparecem com `stderr` populado

**Lição enterprise**: para compliance financeira/regulatória, há um
registro imutável de **qual código foi executado pelo agente em nome do
usuário** — sem necessidade de instrumentação adicional.

---

## Verificação final

Após rodar os 5 atos, valide:

- [ ] `agents-cli run` com `--session-id` reusa sandbox (Ato 1)
- [ ] Tentativas de rede falham (Ato 2)
- [ ] Tentativas de pip install falham (Ato 3)
- [ ] Limites de recursos são enforced (Ato 4)
- [ ] Audit trail visível no Cloud Trace (Ato 5)
- [ ] Cloud Run logs do sandbox-host RE não mostram chamadas falhas
      relacionadas a IAM

## Cleanup (sem full undeploy)

Se você quer resetar sessões sem destruir o agente:

```bash
# Não há comando direto para limpar sessões da console — elas expiram
# automaticamente após inatividade (TTL configurado pelo Agent Runtime).
# Para forçar reset, simplesmente comece nova sessão (omita --session-id).
```

Full teardown:

```bash
./undeploy.sh
```
