# A2A Demo — Architecture

This document explains the A2A (Agent-to-Agent) protocol on Agent Runtime, including the architecture, data flows, stumbling blocks we discovered, and design decisions.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                              Google Cloud Project (vibe-cabral)                     │
│                                                                                     │
│  ┌───────────┐                                                                     │
│  │           │      ┌──────────────────────────────────────────────────────────┐    │
│  │  User /   │      │              Agent Runtime — Orchestrator                │    │
│  │  CLI /    │─────▶│                                                          │    │
│  │  agents-  │      │  ┌──────────────────────────────────────────────────┐    │    │
│  │  cli run  │◀─────│  │  orchestrator_agent (ADK Agent)                  │    │    │
│  │           │      │  │                                                  │    │    │
│  └───────────┘      │  │  Instruction: "Delegue câmbio para specialist"  │    │    │
│                     │  │  sub_agents: [currency_specialist]               │    │    │
│                     │  │                                                  │    │    │
│                     │  │  RemoteA2aAgent ────────────────────────────┐    │    │    │
│                     │  │    name: currency_specialist                │    │    │    │
│                     │  │    agent_card: .../a2a/v1/card              │    │    │    │
│                     │  │    httpx_client: GCP auth (Bearer token)    │    │    │    │
│                     │  └──────────────────────────────────────────┘  │    │    │    │
│                     └───────────────────────────────────────────────┘    │    │    │
│                                                                   │          │    │
│                                                                   │ A2A      │    │
│                                                                   │ Protocol │    │
│                                                                   │ (HTTP)   │    │
│                                                                   ▼          │    │
│                     ┌──────────────────────────────────────────────────────┐  │    │
│                     │              Agent Runtime — Specialist              │  │    │
│                     │                                                      │  │    │
│                     │  ┌──────────────────────────────────────────────┐    │  │    │
│                     │  │  currency_specialist (A2A Agent)              │    │  │    │
│                     │  │                                              │    │  │    │
│                     │  │  Tools:                                      │    │  │    │
│                     │  │  ├── get_exchange_rate(from, to)             │    │  │    │
│                     │  │  └── convert_currency(amount, from, to)      │    │  │    │
│                     │  │                                              │    │  │    │
│                     │  │  Agent Card: /a2a/v1/card                   │    │  │    │
│                     │  │  RPC URL:    /a2a                           │    │  │    │
│                     │  └──────────────────────────────────────────────┘    │  │    │
│                     └──────────────────────────────────────────────────────┘  │    │
│                                                                               │    │
│  ┌────────────────────────────────────────────────────────────────────────┐   │    │
│  │  GCS Staging Bucket  gs://<project-id>-a2a-demo-staging/              │   │    │
│  └────────────────────────────────────────────────────────────────────────┘   │    │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## What is A2A?

A2A (Agent-to-Agent) is a protocol that allows independent agents running as separate services to communicate over HTTP. Unlike local sub-agents that share the same process, A2A agents are deployed independently and discover each other via **agent cards**.

```
┌──────────────────────────────────────────────────────────────────┐
│                    A2A Protocol Flow                              │
│                                                                  │
│  1. DISCOVERY                                                    │
│     Orchestrator fetches specialist's agent card                 │
│     GET /a2a/v1/card → AgentCard JSON                           │
│                                                                  │
│  2. CARD CONTENTS                                                │
│     ┌─────────────────────────────────────────────────┐          │
│     │ {                                               │          │
│     │   "name": "currency_specialist",                │          │
│     │   "description": "Agente de câmbio...",         │          │
│     │   "url": ".../a2a",         ← RPC endpoint     │          │
│     │   "skills": [...],          ← what it can do    │          │
│     │   "protocolVersion": "0.3.0",                   │          │
│     │   "preferredTransport": "HTTP+JSON"             │          │
│     │ }                                               │          │
│     └─────────────────────────────────────────────────┘          │
│                                                                  │
│  3. RPC CALL                                                     │
│     Orchestrator sends A2A message to the RPC URL                │
│     POST /a2a  { "message": { "parts": [...] } }                │
│                                                                  │
│  4. RESPONSE                                                     │
│     Specialist processes, runs tools, returns result             │
│     { "result": { "parts": [{ "text": "525 BRL" }] } }         │
└──────────────────────────────────────────────────────────────────┘
```

---

## ADK A2A Classes

### Server-side: Exposing an Agent

```python
# For local testing — wraps any ADK agent as A2A server
from google.adk.a2a.utils.agent_to_a2a import to_a2a

a2a_app = to_a2a(root_agent, host="0.0.0.0", port=8001)
# Run with: uvicorn app.a2a_app:a2a_app --port 8001
# Card at:  http://localhost:8001/.well-known/agent.json
```

For Agent Runtime deployment, the `adk_a2a` scaffold generates a different entrypoint using `A2aAgent` from `vertexai.preview.reasoning_engines` — Agent Runtime handles the A2A server infrastructure automatically.

### Client-side: Consuming a Remote Agent

```python
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

specialist = RemoteA2aAgent(
    name="currency_specialist",
    description="...",
    agent_card="https://.../a2a/v1/card",  # URL to agent card
    use_legacy=False,                       # use new A2A executor
    httpx_client=auth_httpx_client,         # GCP auth (required on Agent Runtime!)
)

root_agent = Agent(
    name="orchestrator",
    sub_agents=[specialist],  # used like a regular sub-agent
    ...
)
```

---

## Stumbling Blocks & Solutions

### 1. Agent Card URL on Agent Runtime

**Problem:** The ADK local dev server serves the agent card at `/.well-known/agent.json`. We assumed Agent Runtime would use the same path. It doesn't.

**Discovery:** Agent Runtime serves the card at a completely different path:
```
https://{REGION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT}/locations/{REGION}/reasoningEngines/{ID}/a2a/v1/card
```

The `agents-cli deploy` output prints this URL:
```
🪪 Agent Card URL: https://...reasoningEngines/{ID}/a2a/v1/card
```

The RPC endpoint (for sending A2A messages) is at `/a2a`:
```
https://{REGION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT}/locations/{REGION}/reasoningEngines/{ID}/a2a
```

**Solution:** `deploy.sh` constructs the card URL as `{base_url}/a2a/v1/card` and verifies it's accessible before passing to the orchestrator.

### 2. Authentication Between Agent Runtime Instances

**Problem:** `RemoteA2aAgent` uses plain `httpx.AsyncClient` without GCP credentials. When the orchestrator agent (on Agent Runtime) tries to fetch the specialist's agent card, it gets HTTP **401 Unauthorized**.

**Error in logs:**
```
AgentCardResolutionError
HTTP Status/401
```

**Solution:** Inject a custom `httpx.AsyncClient` with GCP auth:

```python
class _GCPAuth(httpx.Auth):
    def __init__(self):
        self._credentials, _ = google.auth.default()

    def auth_flow(self, request):
        self._credentials.refresh(google.auth.transport.requests.Request())
        request.headers["Authorization"] = f"Bearer {self._credentials.token}"
        yield request

auth_client = httpx.AsyncClient(auth=_GCPAuth(), timeout=httpx.Timeout(120))

specialist = RemoteA2aAgent(
    ...,
    httpx_client=auth_client,
)
```

Using `httpx.Auth` ensures tokens are refreshed on every request (tokens expire after 1 hour).

### 3. Agent Framework Detection

**Finding:** When deploying an A2A agent (scaffolded with `--agent adk_a2a`), agents-cli detects the framework as `custom` (not `google-adk`):
```
INFO:vertexai_genai.agentengines:Using agent framework: custom
```

This is because the A2A entrypoint uses `A2aAgent` from `vertexai.preview.reasoning_engines` instead of `AdkApp`. The orchestrator (standard ADK) correctly gets `google-adk`.

### 4. Package Dependencies

**Required for A2A:**
```toml
# Both specialist and orchestrator need:
"google-adk>=1.27.0,<2.0.0"
"a2a-sdk~=0.3.22"

# NOT google-adk[a2a] — the scaffold uses a2a-sdk directly
```

The `adk_a2a` scaffold uses `a2a-sdk` as a separate dependency, not via the `google-adk[a2a]` extra. Both approaches work, but following the scaffold is recommended.

### 5. Local vs Agent Runtime Card Paths

| Environment | Card Path | RPC Path |
|-------------|-----------|----------|
| Local (uvicorn) | `/.well-known/agent.json` | `/` |
| Agent Runtime | `/a2a/v1/card` | `/a2a` |

This means the `SPECIALIST_A2A_CARD_URL` env var changes between local testing and cloud deployment.

### 6. `agents-cli run` Display

**Finding:** When the orchestrator delegates to the specialist, `agents-cli run` shows the specialist's response under `[currency_specialist]:` but the orchestrator's own line `[orchestrator_agent]:` appears empty. This is a display quirk — the agent is working correctly, the orchestrator just doesn't add its own text when relaying.

---

## Deployment Sequence

```
deploy.sh
  │
  ├── Step 1: Create GCS staging bucket
  │
  ├── Step 2: Deploy Specialist (A2A server)
  │   └── agents-cli deploy (framework: custom, is_a2a: true)
  │   └── Writes specialist-agent/deployment_metadata.json
  │
  ├── Step 3: Discover Specialist A2A Card URL
  │   ├── Construct URL: {base}/a2a/v1/card
  │   ├── Verify with curl (expects HTTP 200)
  │   └── Store as SPECIALIST_A2A_CARD_URL
  │
  └── Step 4: Deploy Orchestrator (A2A client)
      └── agents-cli deploy with SPECIALIST_A2A_CARD_URL env var
      └── Writes orchestrator-agent/deployment_metadata.json

undeploy.sh (reverse order)
  │
  ├── Step 1: Delete Orchestrator (depends on specialist)
  ├── Step 2: Delete Specialist
  └── Step 3: Delete staging bucket
```

---

## Agent Card Anatomy (Auto-Generated)

When you scaffold with `--agent adk_a2a`, the `AgentCardBuilder` auto-generates the card from your agent's metadata:

```json
{
  "name": "currency_specialist",
  "description": "Agente especialista em câmbio...",
  "version": "0.1.0",
  "protocolVersion": "0.3.0",
  "preferredTransport": "HTTP+JSON",
  "url": "https://.../a2a",
  "supportsAuthenticatedExtendedCard": true,
  "capabilities": {
    "streaming": false,
    "extensions": [{
      "uri": "https://google.github.io/adk-docs/a2a/a2a-extension/",
      "description": "Ability to use the new agent executor implementation"
    }]
  },
  "skills": [
    { "id": "currency_specialist", "name": "model", "tags": ["llm"] },
    { "id": "currency_specialist-get_exchange_rate", "name": "get_exchange_rate", "tags": ["llm", "tools"] },
    { "id": "currency_specialist-convert_currency", "name": "convert_currency", "tags": ["llm", "tools"] }
  ],
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"]
}
```

Key points:
- **skills** are auto-extracted from agent tools and description
- **url** is set by Agent Runtime to the managed `/a2a` endpoint
- **streaming: false** — Agent Runtime doesn't support A2A streaming yet
- **extensions** — includes the A2A Extension V2 for improved message handling

---

## Design Decisions

### Why two separate agent directories?

Each agent deploys independently to Agent Runtime with its own `deployment_metadata.json`, `pyproject.toml`, and dependencies. They cannot share a single deployment unit.

### Why `httpx.Auth` (not a static token)?

GCP access tokens expire after 1 hour. Using `httpx.Auth` with `credentials.refresh()` ensures the token is refreshed on every request, not just at import time.

### Why `use_legacy=False`?

Enables the A2A Extension V2 executor, which fixes message duplication, output misclassification, and sub-agent data loss. Recommended for all new A2A integrations.

### Why mock tools (not real APIs)?

The demo's purpose is to exercise A2A mechanics, not business logic. Mock data makes the demo deterministic and self-contained.
