# Demo Guide — A2A (Agent-to-Agent)

Roteiro para demonstrar o protocolo A2A entre dois agentes no Agent Runtime.

## Pré-requisitos

```bash
# Ambos os agentes devem estar deployados
cat specialist-agent/deployment_metadata.json   # deve existir
cat orchestrator-agent/deployment_metadata.json  # deve existir
```

## URLs dos agentes

```bash
cd a2a-demo

export SPECIALIST_URL=$(python3 -c "
import json
m = json.load(open('specialist-agent/deployment_metadata.json'))
r = m['remote_agent_runtime_id']
loc = r.split('/')[3]
print(f'https://{loc}-aiplatform.googleapis.com/v1beta1/{r}')
")

export ORCHESTRATOR_URL=$(python3 -c "
import json
m = json.load(open('orchestrator-agent/deployment_metadata.json'))
r = m['remote_agent_runtime_id']
loc = r.split('/')[3]
print(f'https://{loc}-aiplatform.googleapis.com/v1beta1/{r}')
")

echo "Specialist:   $SPECIALIST_URL"
echo "Orchestrator: $ORCHESTRATOR_URL"
```

## Formas de acesso

| Método | Quando usar |
|--------|------------|
| `agents-cli run --url $URL --mode a2a "prompt"` | Testar specialist diretamente via A2A |
| `agents-cli run --url $URL --mode adk "prompt"` | Testar orchestrator (delega via A2A) |
| Console Playground | Demos visuais — links impressos pelo `deploy.sh` |

---

## Ato 1 — Agent Card (Descoberta A2A)

O primeiro passo do protocolo A2A é a **descoberta**: o orchestrator busca o agent card do specialist para saber o que ele faz e como se comunicar.

```bash
# Ver o agent card do specialist no Agent Runtime
curl -s "${SPECIALIST_URL}/a2a/v1/card" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" | python3 -m json.tool
```

**O que observar:**
- `name`: `currency_specialist`
- `skills`: lista dos tools do agente (get_exchange_rate, convert_currency)
- `url`: endpoint RPC para envio de mensagens A2A (termina em `/a2a`)
- `protocolVersion`: `0.3.0`
- `preferredTransport`: `HTTP+JSON`

**Ponto importante:** No Agent Runtime, o card fica em `/a2a/v1/card` — diferente do path local `/.well-known/agent.json`.

---

## Ato 2 — Specialist Direto (A2A Mode)

Teste o specialist diretamente via protocolo A2A, sem passar pelo orchestrator.

```bash
cd specialist-agent

agents-cli run --url $SPECIALIST_URL --mode a2a \
  "Converta 100 USD para BRL"
```

**O que observar:** O specialist chama `convert_currency()` e retorna o resultado. O `--mode a2a` usa o protocolo A2A nativo (não ADK).

```bash
agents-cli run --url $SPECIALIST_URL --mode a2a \
  "Qual a taxa de câmbio de EUR para GBP?"
```

**O que observar:** O specialist chama `get_exchange_rate()`. Mostra que as duas tools funcionam via A2A.

---

## Ato 3 — Orchestrator Delegando via A2A

Agora teste o orchestrator — ele recebe a pergunta e decide se delega para o specialist ou responde diretamente.

### Pergunta de câmbio (delega para specialist)

```bash
cd orchestrator-agent

agents-cli run --url $ORCHESTRATOR_URL --mode adk \
  "Quanto é 500 euros em reais?"
```

**O que observar:**
- `[orchestrator_agent]:` — pode aparecer vazio (comportamento normal do agents-cli com sub-agents)
- `[currency_specialist]:` — resposta do specialist com o resultado da conversão
- O orchestrator **não** tem tools de câmbio — ele delegou via A2A para o specialist

### Pergunta geral (responde direto)

```bash
agents-cli run --url $ORCHESTRATOR_URL --mode adk \
  "Qual a capital do Brasil?"
```

**O que observar:** O orchestrator responde diretamente como `[orchestrator_agent]:`, sem delegar. Mostra que a decisão de delegação é do LLM baseada nas instructions.

---

## Ato 4 — Verificação nos Traces

Confirme que a comunicação A2A realmente atravessa a rede (não é delegação local).

1. **Console > Cloud Trace > Trace Explorer**
2. Filtrar por `reasoningEngines/<ORCHESTRATOR_ID>`
3. Procurar spans que mostrem a chamada A2A saindo do orchestrator para o specialist

**O que observar no trace do orchestrator:**
```
invocation
└── agent_run: orchestrator_agent
    ├── call_llm (decide delegar)
    └── transfer_to_agent: currency_specialist  ← A2A call
```

---

## Resumo da Arquitetura A2A

| Componente | Papel | Deploy |
|-----------|-------|--------|
| **Specialist** | Servidor A2A — expõe agent card e endpoint RPC | `agents-cli deploy` com `adk_a2a` scaffold |
| **Orchestrator** | Cliente A2A — descobre specialist via card, envia mensagens | `agents-cli deploy` com `adk` scaffold + `RemoteA2aAgent` |

### Achados importantes desta demo

| Achado | Detalhe |
|--------|---------|
| **Card URL no Agent Runtime** | `/a2a/v1/card` (não `/.well-known/agent.json`) |
| **Auth obrigatória** | `RemoteA2aAgent` precisa de `httpx_client` com GCP Bearer token |
| **Framework detection** | Specialist detectado como `custom` (não `google-adk`) |
| **Streaming** | A2A no Agent Runtime não suporta streaming ainda |

---

## Limpeza

```bash
# Undeploy ambos os agentes e deletar bucket
./undeploy.sh
```
