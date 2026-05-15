# Demo Guide — SPIFFE Identity + Agent Registry Discovery

Roteiro para demonstrar SPIFFE identities, auto-registro no Agent Registry, e descoberta dinâmica via registry.

## Pré-requisitos

```bash
# Ambos os agentes devem estar deployados com --agent-identity
cat specialist-agent/deployment_metadata.json   # deve existir
cat orchestrator-agent/deployment_metadata.json  # deve existir
```

## URLs dos agentes

```bash
cd spiffe-registry-demo

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
| `agents-cli run --url $URL --mode adk "prompt"` | Testar orchestrator (descobre specialist via registry) |
| Console Playground | Demos visuais — links impressos pelo `deploy.sh` |
| `gcloud alpha agent-registry agents list` | Inspecionar registros no Agent Registry |

---

## Ato 1 — SPIFFE Identity (Identidade dos Agentes)

O primeiro conceito é a identidade SPIFFE: cada agente recebe uma identidade criptográfica única, diferente do service account compartilhado.

### Verificar identidade via API

```bash
# Extrair o RE ID do specialist
SPECIALIST_RE_ID=$(python3 -c "
import json
m = json.load(open('specialist-agent/deployment_metadata.json'))
print(m['remote_agent_runtime_id'].split('/')[-1])
")

# Buscar a identidade efetiva
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')
curl -s "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/us-central1/reasoningEngines/${SPECIALIST_RE_ID}" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" | python3 -m json.tool | grep effectiveIdentity
```

**O que observar:**
- `effectiveIdentity`: deve conter um principal SPIFFE no formato `agents.global.org-{ORG_ID}.system.id.goog/resources/aiplatform/projects/{NUM}/locations/{REGION}/reasoningEngines/{ID}`
- Cada agente tem uma identidade **única** — diferente do service account compartilhado `service-{NUM}@gcp-sa-aiplatform-re.iam.gserviceaccount.com`

**Ponto importante:** Com `--agent-identity`, o agente usa certificados X.509 que são renovados automaticamente a cada 24h. Tokens de acesso são vinculados ao certificado (DPoP), impedindo reuso se roubados.

---

## Ato 2 — Agent Registry (Auto-Registro)

Agentes deployados no Agent Runtime são **automaticamente registrados** no Agent Registry. Agentes A2A têm suas skills extraídas do agent card.

### Listar agentes no registry

```bash
gcloud alpha agent-registry agents list \
  --location=us-central1 \
  --project=$(gcloud config get-value project)
```

**O que observar:**
- O specialist aparece com `displayName: currency_specialist`
- Protocolo: `A2A_AGENT` (não CUSTOM)
- Skills listadas: `get_exchange_rate`, `convert_currency`
- `RuntimeIdentity.principal`: deve ser `principal://...` (SPIFFE), não `sa://...`

### Comparar com agentes sem SPIFFE

```bash
# Agentes do a2a-demo (sem --agent-identity) mostram:
#   RuntimeIdentity.principal: sa://service-280799742875@gcp-sa-aiplatform-re.iam.gserviceaccount.com
#
# Agentes do spiffe-registry-demo (com --agent-identity) mostram:
#   RuntimeIdentity.principal: principal://agents.global.org-{ORG}.../reasoningEngines/{ID}
```

### Inspecionar agent card no registry

```bash
# Descrever o specialist no registry
gcloud alpha agent-registry agents describe \
  $(gcloud alpha agent-registry agents list --location=us-central1 \
    --filter="displayName='currency_specialist'" --format='value(name)' | head -1) \
  --location=us-central1
```

**O que observar:** O campo `card.content` contém o agent card completo — skills, capabilities, URL do endpoint A2A, versão do protocolo.

---

## Ato 3 — Specialist Direto (A2A Mode)

Teste o specialist diretamente, igual ao a2a-demo.

```bash
cd specialist-agent

agents-cli run --url $SPECIALIST_URL --mode a2a \
  "Converta 100 USD para BRL"
```

**O que observar:** Funcionamento idêntico ao a2a-demo. A diferença está na identidade subjacente (SPIFFE vs SA), não no comportamento visível.

```bash
agents-cli run --url $SPECIALIST_URL --mode a2a \
  "Qual a taxa de câmbio de EUR para GBP?"
```

---

## Ato 4 — Orchestrator com Descoberta via Registry

O orchestrator descobre o specialist dinamicamente via Agent Registry — sem URL hardcoded.

### Pergunta de câmbio (delega via A2A, descoberto via registry)

```bash
cd orchestrator-agent

agents-cli run --url $ORCHESTRATOR_URL --mode adk \
  "Quanto é 500 euros em reais?"
```

**O que observar:**
- O orchestrator encontrou o specialist **via Agent Registry** (não via URL fixa)
- A delegação A2A funciona normalmente — `[currency_specialist]:` com o resultado
- No código do orchestrator, `_discover_specialist()` usou `AgentRegistry.get_remote_a2a_agent()`

### Pergunta geral (responde direto)

```bash
agents-cli run --url $ORCHESTRATOR_URL --mode adk \
  "Qual a capital do Brasil?"
```

**O que observar:** O orchestrator responde diretamente, sem delegar. A lógica de delegação é do LLM.

---

## Ato 5 — Verificação nos Traces

### Cloud Trace

1. **Console > Cloud Trace > Trace Explorer**
2. Filtrar por `reasoningEngines/<ORCHESTRATOR_ID>`
3. Verificar spans da chamada A2A

**O que observar no trace:**
```
invocation
└── agent_run: orchestrator_agent
    ├── call_llm (decide delegar)
    └── transfer_to_agent: currency_specialist  ← A2A call via registry discovery
```

### Cloud Audit Logs

Com SPIFFE, os audit logs mostram a identidade específica do agente:
1. **Console > Logging > Logs Explorer**
2. Filtrar: `protoPayload.authenticationInfo.principalEmail` contendo `agents.global.org`
3. Verificar que cada agente tem um principal diferente

---

## Resumo

| Conceito | O que esta demo mostra |
|----------|----------------------|
| **SPIFFE Identity** | Cada agente tem identidade criptográfica única via `--agent-identity` |
| **Auto-Registration** | Agentes aparecem automaticamente no Agent Registry após deploy |
| **A2A Card Extraction** | Registry extrai skills e capabilities do agent card A2A |
| **Dynamic Discovery** | Orchestrator descobre specialist via `AgentRegistry.get_remote_a2a_agent()` |
| **Identity in Registry** | `RuntimeIdentity` mostra `principal://` (SPIFFE) vs `sa://` (default) |

---

## Limpeza

```bash
./undeploy.sh
```
