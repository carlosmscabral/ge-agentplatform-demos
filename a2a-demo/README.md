# Google Agent Platform A2A Demo

Demonstra o protocolo **A2A (Agent-to-Agent)** no Agent Runtime — dois agentes independentes se comunicando via A2A protocol.

Para detalhes da arquitetura, achados e problemas resolvidos, veja [ARCHITECTURE.md](ARCHITECTURE.md). Para o roteiro de demonstração, veja [DEMO.md](DEMO.md).

## Arquitetura

```
┌──────────────┐     ┌───────────────────────┐     ┌───────────────────────┐
│  User / CLI  │────▶│  Orchestrator Agent   │────▶│  Specialist Agent    │
│              │◀────│  (ADK — delega)        │◀────│  (A2A — câmbio)      │
└──────────────┘     └───────────────────────┘     └───────────────────────┘
                           Agent Runtime               Agent Runtime
                           modo: adk                   modo: a2a
```

1. **Specialist Agent**: Agente de câmbio com tools mock (`convert_currency`, `get_exchange_rate`). Exposto como serviço A2A.
2. **Orchestrator Agent**: Agente orquestrador que delega perguntas de câmbio para o specialist via `RemoteA2aAgent`.

## Quick Start

### Deploy

```bash
cp .env.template .env        # Preencher PROJECT_ID
./deploy.sh                  # Deploya specialist primeiro, depois orchestrator
```

### Testar

```bash
# Specialist direto (A2A mode)
cd specialist-agent
agents-cli run --url <specialist-url> --mode a2a "Converta 100 USD para BRL"

# Orchestrator (delega via A2A)
cd orchestrator-agent
agents-cli run --url <orchestrator-url> --mode adk "Qual a cotação do dólar?"
```

### Cleanup

```bash
./undeploy.sh
```

## Configuração

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT_ID` | auto-detected | GCP project ID |
| `REGION` | `us-central1` | GCP region |
| `SPECIALIST_DISPLAY_NAME` | `a2a-demo-specialist` | Nome do specialist |
| `ORCHESTRATOR_DISPLAY_NAME` | `a2a-demo-orchestrator` | Nome do orchestrator |
| `STAGING_BUCKET` | `gs://<PROJECT_ID>-a2a-demo-staging` | GCS staging bucket |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Modelo Gemini |
| `SPECIALIST_A2A_CARD_URL` | auto-populated by deploy.sh | URL do agent card A2A |

## Achados Principais

| # | Achado | Detalhe |
|---|--------|---------|
| 1 | **Card URL no Agent Runtime** | Path é `/a2a/v1/card`, não `/.well-known/agent.json` |
| 2 | **Auth entre agentes** | `RemoteA2aAgent` precisa de `httpx_client` com GCP Bearer token — sem isso, 401 |
| 3 | **Framework: `custom`** | A2A agents são detectados como `custom`, não `google-adk` |
| 4 | **Sem streaming** | A2A no Agent Runtime não suporta streaming ainda |
| 5 | **Token refresh** | Usar `httpx.Auth` com `credentials.refresh()` para renovar tokens automaticamente |
| 6 | **Deploy sequencial** | Specialist deve ser deployado primeiro — orchestrator precisa da URL do card |

Veja [ARCHITECTURE.md](ARCHITECTURE.md) para detalhes completos.
