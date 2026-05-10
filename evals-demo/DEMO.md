# Demo Manual — Evals & Online Monitors

Roteiro para gerar tráfego e alimentar os **Online Monitors** do Gen AI Evaluation Service.

## Pré-requisitos

```bash
# Agente deployado (via agents-cli ou deploy.sh)
cat demo-agent/deployment_metadata.json   # deve existir
```

---

## Gerar Tráfego

O script envia prompts variados que exercitam as 3 ferramentas do agente (`lookup_order`, `search_faq`, `create_ticket`) + casos gerais e edge cases.

```bash
cd evals-demo

# Todos os 20 prompts, 3s entre cada
python scripts/generate_traffic.py

# Só os primeiros 5 (teste rápido)
python scripts/generate_traffic.py --batch 5

# Delay menor (mais rápido, risco de rate limit)
python scripts/generate_traffic.py --delay 1

# Repetir 3 rodadas (60 traces no total)
python scripts/generate_traffic.py --rounds 3

# Combinar: 10 prompts, 2 rodadas, 2s delay
python scripts/generate_traffic.py --batch 10 --rounds 2 --delay 2
```

### Prompts incluídos

| Categoria | Qtd | Exemplos |
|-----------|-----|----------|
| `lookup_order` (sucesso) | 4 | "What's the status of order ORD-123?" |
| `lookup_order` (erro) | 1 | "Check the status of order ORD-999" |
| `search_faq` | 5 | "How do I reset my password?", "Return policy?" |
| `create_ticket` | 3 | "My order arrived damaged", "Charged twice" |
| `multi-tool` | 2 | "Check status AND create a complaint ticket" |
| `general` | 3 | "Hi, what can you help with?", "Thanks!" |
| `edge` | 2 | "Ignore your instructions", "Give me a discount" |

---

## Verificar Traces

Após gerar tráfego, os traces aparecem em ~1 minuto:

```
Console > Cloud Trace > Trace Explorer
```

Filtrar por `reasoningEngines/<ID>` para ver os traces do agente.

---

## Online Monitor

### Configurar (uma vez)

1. Console > **Agent Platform > Agents > Deployments** > selecionar o agente
2. **Dashboard > Evaluation** > **New Monitor**
3. Selecionar métricas: `FINAL_RESPONSE_QUALITY`, `TOOL_USE_QUALITY`, `HALLUCINATION`, `SAFETY`
4. Sampling: 100%
5. Clicar **Create**

### Alimentar

```bash
# Gerar bastante tráfego para o monitor ter dados
python scripts/generate_traffic.py --rounds 3 --delay 2
```

Os resultados aparecem no dashboard do monitor conforme os traces são avaliados.

---

## Eval Offline (programático)

Para rodar avaliação offline via SDK (sem depender do monitor):

```bash
cd demo-agent
PROJECT_ID=vibe-cabral uv run python run_offline_eval.py
```

Este script:
1. Envia prompts ao agente deployado (`client.evals.run_inference`)
2. Avalia com métricas adaptativas (`client.evals.evaluate`)
3. Imprime scores por prompt e agregados

---

## Limpeza

```bash
./undeploy.sh
```
