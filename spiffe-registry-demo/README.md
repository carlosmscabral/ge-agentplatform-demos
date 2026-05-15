# SPIFFE + Agent Registry Demo

Demonstrates **SPIFFE agent identity** and **Agent Registry discovery** on Agent Runtime — two agents communicating via A2A protocol, discovered dynamically through the registry instead of hardcoded URLs.

For architecture details, see [ARCHITECTURE.md](ARCHITECTURE.md). For the demo walkthrough, see [DEMO.md](DEMO.md).

## What This Demo Explores

1. **SPIFFE Identity** — Deploy agents with `--agent-identity` to provision unique SPIFFE identities (X.509 certificates, DPoP tokens)
2. **Auto-Registration** — Verify agents automatically appear in Agent Registry with their SPIFFE principal
3. **Dynamic Discovery** — Orchestrator discovers the specialist via `AgentRegistry.get_remote_a2a_agent()` instead of a hardcoded URL
4. **A2A with Identity** — Inter-agent communication authenticated with SPIFFE credentials

## Architecture

```
┌──────────────┐     ┌───────────────────────┐     ┌───────────────────────┐
│  User / CLI  │────▶│  Orchestrator Agent   │────▶│  Specialist Agent    │
│              │◀────│  (SPIFFE identity)     │◀────│  (SPIFFE identity)   │
└──────────────┘     └──────────┬────────────┘     └───────────────────────┘
                                │                         ▲
                                │  discovers via          │ auto-registered
                                ▼                         │
                     ┌───────────────────────┐            │
                     │   Agent Registry      │────────────┘
                     │   (auto-populated)    │
                     └───────────────────────┘
```

## Quick Start

### Deploy

```bash
cp .env.template .env        # Set PROJECT_ID
./deploy.sh                  # Deploys both agents with SPIFFE identity
```

### Test

```bash
# Specialist directly (A2A mode)
cd specialist-agent
agents-cli run --url <specialist-url> --mode a2a "Converta 100 USD para BRL"

# Orchestrator (discovers specialist via registry)
cd orchestrator-agent
agents-cli run --url <orchestrator-url> --mode adk "Qual a cotação do dólar?"
```

### Inspect Registry

```bash
gcloud alpha agent-registry agents list --location=us-central1
```

### Cleanup

```bash
./undeploy.sh
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT_ID` | auto-detected | GCP project ID |
| `REGION` | `us-central1` | GCP region |
| `SPECIALIST_DISPLAY_NAME` | `spiffe-specialist` | Specialist agent name |
| `ORCHESTRATOR_DISPLAY_NAME` | `spiffe-orchestrator` | Orchestrator agent name |
| `STAGING_BUCKET` | `gs://<PROJECT_ID>-spiffe-registry-staging` | GCS staging bucket |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Gemini model |
| `REGISTRY_LOCATION` | `us-central1` | Agent Registry location for discovery |
| `SPECIALIST_REGISTRY_NAME` | auto-populated by deploy.sh | Agent Registry resource name |
| `SPECIALIST_A2A_CARD_URL` | auto-populated by deploy.sh | Fallback A2A card URL |

## Key Differences from a2a-demo

| Aspect | a2a-demo | spiffe-registry-demo |
|--------|----------|---------------------|
| Identity | Default SA | SPIFFE (`--agent-identity`) |
| Discovery | Hardcoded URL | `AgentRegistry.get_remote_a2a_agent()` |
| Registry | Not used | Auto-registration + dynamic discovery |
| deploy.sh | 4 steps | 6 steps (adds registry verification) |

## Key Findings

| # | Finding | Detail |
|---|---------|--------|
| 1 | **Auto-registration** | Agents deployed to Agent Runtime appear in Agent Registry automatically |
| 2 | **A2A card extraction** | A2A agents get full agent card + skills indexed in registry |
| 3 | **`--agent-identity` flag** | `agents-cli deploy --agent-identity` provisions SPIFFE identity natively |
| 4 | **Registry discovery** | `AgentRegistry.get_remote_a2a_agent()` creates `RemoteA2aAgent` from registry entry |
| 5 | **`agent-identity` extra** | `AgentRegistry` requires `google-adk[agent-identity]` — without it, import fails |
| 6 | **CAA blocks A2A** | SPIFFE tokens are certificate-bound; A2A via plain HTTP gets 403. Workaround: `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False` |
| 7 | **Baseline IAM required** | SPIFFE agents only get 2 auto-granted roles — must manually grant `aiplatform.user`, `serviceusage.serviceUsageConsumer`, etc. via principal set |
| 8 | **SPIFFE in registry** | `RuntimeIdentity.principal` changes from `sa://...` to `principal://agents.global.org-...` |
| 9 | **`spec.effectiveIdentity`** | SPIFFE ID is at `spec.effectiveIdentity` in the RE API, not at the root level |

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed findings.
