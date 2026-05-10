# Demo Manual — Sessions & Memory Bank

Roteiro para demonstrar as duas camadas de persistência do Agent Platform:
**Session State** (preferências explícitas) vs **Memory Bank** (insights extraídos automaticamente).

## Pré-requisitos

```bash
cd sessions-memory-demo/demo-agent

# Limpar dados de demos anteriores (mantém o agente deployado)
uv run python ../scripts/cleanup_sessions_memories.py
```

## URL do agente

```bash
# Pegar a URL completa do agente deployado
export AGENT_URL=$(python3 -c "
import json
m = json.load(open('deployment_metadata.json'))
r = m['remote_agent_runtime_id']
loc = r.split('/')[3]
print(f'https://{loc}-aiplatform.googleapis.com/v1beta1/{r}')
")
echo $AGENT_URL
```

---

## Ato 1 — Primeiro Contato

O cliente se apresenta, salva preferências e cria um ticket.
O agente salva preferências no `user:` state e o Memory Bank captura os detalhes da conversa automaticamente.

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Oi! Meu nome é Carlos, meu customer ID é cust_001. Prefiro receber notificações por Slack."
```

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Pode consultar os detalhes da minha conta?"
```

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Percebi que fui cobrado duas vezes na última fatura — R$500 a mais. Cria um ticket de prioridade alta sobre essa cobrança duplicada, por favor."
```

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Ah, e por favor anota: sempre me avise antes de fazer qualquer alteração de billing na minha conta."
```

**Esperar ~20 segundos** para o Memory Bank extrair os fatos da conversa.

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

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Qual canal de notificação eu prefiro?"
```

**O que observar:** o agente chama `get_preferences()` e retorna `preferred_name=Carlos`, `notification_channel=slack` — dados salvos explicitamente no Ato 1 via `update_preference()`.

---

## Ato 3 — Memory Bank (histórico de conversa)

Nova sessão. O cliente pergunta sobre problemas anteriores — informação que **não está** no `user:` state. Só o Memory Bank pode responder.

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Tive um problema de suporte recentemente. Pode me lembrar do que se tratava?"
```

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Criamos um ticket pra isso? Qual era a prioridade?"
```

**O que observar:** o agente lembra da cobrança duplicada de R$500, do ticket de alta prioridade, e da instrução de avisar antes de mudanças de billing — tudo extraído automaticamente pelo Memory Bank, não salvo como preferência.

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
