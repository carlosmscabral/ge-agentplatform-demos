# Demo Guide — Sessions & Memory Bank

Roteiro para demonstrar as duas camadas de persistência do Agent Platform:
**Session State** (preferências explícitas) e **Memory Bank** (insights extraídos automaticamente).

## Pré-requisitos

```bash
cd sessions-memory-demo/demo-agent

# Limpar dados de demos anteriores (mantém o agente deployado)
uv run python ../scripts/cleanup_sessions_memories.py
```

## URL do agente

```bash
export AGENT_URL=$(python3 -c "
import json
m = json.load(open('deployment_metadata.json'))
r = m['remote_agent_runtime_id']
loc = r.split('/')[3]
print(f'https://{loc}-aiplatform.googleapis.com/v1beta1/{r}')
")
echo $AGENT_URL
```

## Formas de acesso

| Método | Quando usar |
|--------|------------|
| `agents-cli run --url $AGENT_URL --mode adk "prompt"` | Principal — funciona de qualquer terminal |
| Console Playground | Demos visuais — link impresso pelo `deploy.sh` |
| `uv run python ../scripts/demo_stateless.py` | Cenário A automatizado (local, sem persistência) |
| `uv run python ../scripts/demo_stateful.py` | Cenário B automatizado (deployado, com persistência) |

---

## Ato 1 — Primeiro Contato

O cliente se apresenta, salva preferências e cria um ticket.
O agente salva preferências no `user:` state e o Memory Bank captura os detalhes da conversa automaticamente.

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Oi! Meu nome é Carlos, meu customer ID é cust_001. Prefiro receber notificações por Slack."
```

**O que observar:** O agente chama `update_preference()` para salvar `preferred_name`, `customer_id` e `notification_channel` no state com prefixo `user:`.

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Pode consultar os detalhes da minha conta?"
```

**O que observar:** O agente chama `lookup_account(cust_001)` — retorna plano Enterprise, status de cobrança e features.

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Percebi que fui cobrado duas vezes na última fatura — R$500 a mais. Cria um ticket de prioridade alta sobre essa cobrança duplicada, por favor."
```

**O que observar:** O agente chama `create_ticket()` com prioridade alta. O ticket é criado e o agente confirma o ID.

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Ah, e por favor anota: sempre me avise antes de fazer qualquer alteração de billing na minha conta."
```

**O que observar:** O agente reconhece uma instrução explícita. O Memory Bank vai capturar isso como `EXPLICIT_INSTRUCTIONS`.

**Esperar ~20 segundos** para o Memory Bank processar a conversa.

```bash
echo "Aguardando Memory Bank processar..." && sleep 20
```

---

## Ato 2 — Session State (preferências persistem)

Nova sessão, mesmo usuário. O agente deve lembrar nome e canal de notificação via `user:` state.

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "E aí, sou eu de novo. Você lembra quem eu sou?"
```

**O que observar:** O agente chama `get_preferences()` e retorna `preferred_name=Carlos`, `notification_channel=slack` — dados salvos explicitamente no Ato 1.

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Qual canal de notificação eu prefiro?"
```

**O que observar:** Resposta imediata — "Slack" — sem precisar consultar o Memory Bank. Dado estruturado no session state.

---

## Ato 3 — Memory Bank (histórico de conversa)

Nova sessão. O cliente pergunta sobre problemas anteriores — informação que **não está** no `user:` state. Só o Memory Bank pode responder.

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Tive um problema de suporte recentemente. Pode me lembrar do que se tratava?"
```

**O que observar:** O `PreloadMemoryTool` injeta automaticamente as memórias do Memory Bank. O agente lembra da cobrança duplicada de R$500.

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Criamos um ticket pra isso? Qual era a prioridade?"
```

**O que observar:** O agente lembra do ticket de prioridade alta — informação extraída automaticamente pelo Memory Bank, não salva como preferência.

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Quero fazer uma mudança no meu plano de billing."
```

**O que observar:** O momento "wow" — o agente lembra da instrução explícita do Ato 1: "sempre me avise antes de fazer qualquer alteração de billing". Isso veio do tópico `EXPLICIT_INSTRUCTIONS` do Memory Bank.

---

## Verificação

1. **Cloud Trace** — Console > Cloud Trace > Trace Explorer
   - Filtrar por `reasoningEngines/<ID>`
   - Verificar spans de `call_llm`, `execute_tool`, e GenAI events

2. **Session State** — Visível via `get_preferences()` no trace
   - Deve conter: `preferred_name`, `customer_id`, `notification_channel`

3. **Memory Bank** — Visível via `PreloadMemoryTool` no trace do Ato 3
   - Deve conter: detalhes da cobrança duplicada, ticket, instrução de billing

---

## Resumo

| Camada | O que guarda | Como salva | Como recupera |
|--------|-------------|------------|---------------|
| **`user:` state** | Preferências estruturadas (nome, canal, ID) | `update_preference()` — agente salva explicitamente | `get_preferences()` |
| **Memory Bank** | Insights de conversa (problemas, tickets, instruções) | `add_session_to_memory()` — LLM extrai automaticamente | `PreloadMemoryTool` injeta no início da sessão |

---

## Limpeza

```bash
# Apagar todas as sessões e memórias (sem deletar o agente)
uv run python ../scripts/cleanup_sessions_memories.py

# Ou só ver o que seria apagado
uv run python ../scripts/cleanup_sessions_memories.py --dry-run
```
