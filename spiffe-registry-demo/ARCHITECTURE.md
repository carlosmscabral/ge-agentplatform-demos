# SPIFFE + Registry Demo — Architecture

This document explains SPIFFE agent identity, Agent Registry auto-registration, and dynamic discovery — including how they work together for A2A communication on Agent Runtime.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                              Google Cloud Project                                   │
│                                                                                     │
│  ┌───────────┐                                                                     │
│  │           │      ┌──────────────────────────────────────────────────────────┐    │
│  │  User /   │      │              Agent Runtime — Orchestrator                │    │
│  │  CLI /    │─────▶│              SPIFFE Identity ✓                           │    │
│  │  agents-  │      │                                                          │    │
│  │  cli run  │◀─────│  ┌──────────────────────────────────────────────────┐    │    │
│  │           │      │  │  orchestrator_agent (ADK Agent)                  │    │    │
│  └───────────┘      │  │                                                  │    │    │
│                     │  │  1. Queries Agent Registry for A2A agents        │    │    │
│                     │  │  2. Discovers currency_specialist                │    │    │
│                     │  │  3. Creates RemoteA2aAgent from registry         │    │    │
│                     │  │  4. Delegates via A2A protocol                   │    │    │
│                     │  └──────────────────────────────────────────────────┘    │    │
│                     └──────────────────────┬───────────────────────────────────┘    │
│                                            │                                        │
│                           ┌────────────────┴────────────────┐                      │
│                           │                                 │                      │
│                           ▼                                 ▼                      │
│        ┌──────────────────────────────┐    ┌───────────────────────────────────┐   │
│        │      Agent Registry          │    │      Agent Runtime — Specialist   │   │
│        │                              │    │      SPIFFE Identity ✓            │   │
│        │  Auto-populated entries:     │    │                                   │   │
│        │  ├── currency_specialist     │    │  Tools:                           │   │
│        │  │   type: A2A_AGENT         │    │  ├── get_exchange_rate()          │   │
│        │  │   skills: [...]           │    │  └── convert_currency()           │   │
│        │  │   card: {...}             │    │                                   │   │
│        │  │   identity: principal://  │    │  Agent Card: /a2a/v1/card         │   │
│        │  └── orchestrator_agent      │    │  RPC URL:    /a2a                 │   │
│        │      type: CUSTOM            │    └───────────────────────────────────┘   │
│        └──────────────────────────────┘                                            │
│                                                                                     │
│  ┌────────────────────────────────────────────────────────────────────────┐         │
│  │  GCS Staging Bucket  gs://<project-id>-spiffe-registry-staging/       │         │
│  └────────────────────────────────────────────────────────────────────────┘         │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## SPIFFE Identity

### What is SPIFFE?

SPIFFE (Secure Production Identity Framework For Everyone) is an open standard for workload identity. On Google Cloud Agent Platform, each agent gets a unique, cryptographically verifiable identity.

### SPIFFE ID Format

```
principal://agents.global.org-{ORG_ID}.system.id.goog/resources/aiplatform/projects/{PROJECT_NUMBER}/locations/{REGION}/reasoningEngines/{ENGINE_ID}
```

Example:
```
principal://agents.global.org-970548019292.system.id.goog/resources/aiplatform/projects/280799742875/locations/us-central1/reasoningEngines/4602499049021505536
```

### Identity Lifecycle

```
┌────────────────────────────────────────────────────────────────┐
│                   SPIFFE Identity Lifecycle                      │
│                                                                  │
│  1. DEPLOY with --agent-identity                                │
│     └── agents-cli deploy --agent-identity                      │
│         └── Agent Runtime provisions SPIFFE identity             │
│                                                                  │
│  2. CERTIFICATE PROVISIONING                                    │
│     └── X.509 certificate auto-provisioned (24h validity)       │
│     └── Auto-rotated by Agent Runtime                           │
│     └── Certificate-bound access tokens (DPoP)                  │
│                                                                  │
│  3. IAM GRANTS (auto)                                           │
│     └── roles/aiplatform.agentDefaultAccess                     │
│     └── roles/aiplatform.agentContextEditor                     │
│                                                                  │
│  4. RUNTIME                                                     │
│     └── Agent uses SPIFFE identity for all API calls            │
│     └── Tokens are certificate-bound (prevent theft)            │
│     └── Mutual TLS for gateway access                           │
│                                                                  │
│  5. AUDIT                                                       │
│     └── SPIFFE ID appears in Cloud Audit Logs                   │
│     └── Clear provenance: which agent did what                  │
└────────────────────────────────────────────────────────────────┘
```

### SA vs SPIFFE Identity

| Aspect | Default (SA) | SPIFFE (`--agent-identity`) |
|--------|-------------|---------------------------|
| Identity | Shared RE service account | Unique per-agent SPIFFE ID |
| Format | `sa://service-{NUM}@gcp-sa-aiplatform-re.iam.gserviceaccount.com` | `principal://agents.global.org-{ORG}.../reasoningEngines/{ID}` |
| Registry display | `RuntimeIdentity.principal: sa://...` | `RuntimeIdentity.principal: principal://...` |
| IAM granularity | All agents share same SA permissions | Per-agent IAM bindings possible |
| Token binding | Standard bearer token | Certificate-bound (DPoP), unreplayable |
| Agent Gateway | Not compatible | Required for gateway integration |

---

## Agent Registry

### Auto-Registration

When you deploy an agent to Agent Runtime (Vertex AI Agent Engine), it is **automatically registered** in Agent Registry — no extra configuration needed.

```
┌──────────────────────────────────────────────────────────────────┐
│                  Auto-Registration Flow                           │
│                                                                  │
│  agents-cli deploy --agent-identity                              │
│         │                                                        │
│         ▼                                                        │
│  Agent Runtime creates Reasoning Engine                          │
│         │                                                        │
│         ├── Framework detected: google-adk | custom              │
│         ├── If A2A scaffold: fetch agent card at /a2a/v1/card    │
│         │   └── Extract skills, capabilities, description        │
│         │                                                        │
│         ▼                                                        │
│  Agent Registry syncs (within seconds/minutes)                   │
│         │                                                        │
│         ├── Creates Agent resource with auto-generated UUID      │
│         │   name: projects/{P}/locations/{L}/agents/agentregistry-{UUID}
│         │                                                        │
│         ├── Sets protocol info:                                  │
│         │   └── A2A agents → type: A2A_AGENT + card content      │
│         │   └── ADK agents → type: CUSTOM + :query/:streamQuery  │
│         │                                                        │
│         ├── Records identity:                                    │
│         │   └── SA deploy → RuntimeIdentity: sa://...            │
│         │   └── SPIFFE deploy → RuntimeIdentity: principal://... │
│         │                                                        │
│         └── Indexes skills (A2A only)                            │
│             └── From agent card's skills array                   │
└──────────────────────────────────────────────────────────────────┘
```

### Registry Data Model

```
Agent (read-only)
├── name: projects/{P}/locations/{L}/agents/agentregistry-{UUID}
├── agentId: urn:agent:projects-{NUM}:projects:{NUM}:locations:{R}:aiplatform:reasoningEngines:{ID}
├── displayName: currency_specialist
├── description: "Agente especialista em câmbio..."
├── attributes:
│   ├── Framework: { framework: "custom" | "google-adk" }
│   ├── RuntimeIdentity: { principal: "sa://..." | "principal://..." }
│   └── RuntimeReference: { uri: "//aiplatform.googleapis.com/..." }
├── protocols:
│   ├── { type: A2A_AGENT, interfaces: [{ url: ".../a2a" }] }
│   └── { type: CUSTOM, interfaces: [{ url: "...:query" }, { url: "...:streamQuery" }] }
├── card: (A2A only)
│   └── content: { name, skills, capabilities, url, protocolVersion }
└── skills: (A2A only)
    ├── { id: "currency_specialist", name: "model", tags: ["llm"] }
    ├── { id: "currency_specialist-get_exchange_rate", name: "get_exchange_rate" }
    └── { id: "currency_specialist-convert_currency", name: "convert_currency" }
```

### Discovery via ADK

The `AgentRegistry` class in the ADK provides programmatic access to the registry:

```python
from google.adk.integrations.agent_registry import AgentRegistry

registry = AgentRegistry(project_id="my-project", location="us-central1")

# List all agents
agents = registry.list_agents()

# Get a specific agent's metadata
info = registry.get_agent_info("projects/P/locations/L/agents/agentregistry-UUID")

# Create a RemoteA2aAgent directly from registry
specialist = registry.get_remote_a2a_agent(
    agent_name="projects/P/locations/L/agents/agentregistry-UUID",
    httpx_client=auth_httpx_client,  # GCP auth for inter-agent calls
)
```

The `get_remote_a2a_agent()` method:
1. Fetches the agent's metadata from the registry
2. Extracts the A2A card URL from the protocols section
3. Creates a `RemoteA2aAgent` instance with the correct card URL
4. Optionally accepts an `httpx_client` for authentication

### Discovery via gcloud

```bash
# List all agents
gcloud alpha agent-registry agents list --location=us-central1

# Search by keyword
gcloud alpha agent-registry agents search --location=us-central1 --search-string="currency"

# Describe a specific agent
gcloud alpha agent-registry agents describe AGENT_NAME --location=us-central1
```

---

## Dynamic Discovery Flow

This is the key innovation over the `a2a-demo`: the orchestrator discovers the specialist at runtime via Agent Registry instead of using a hardcoded URL.

```
┌────────────────────────────────────────────────────────────────────┐
│               Dynamic Discovery Sequence                           │
│                                                                    │
│  DEPLOY TIME (deploy.sh):                                          │
│                                                                    │
│  1. Deploy specialist with --agent-identity                        │
│  2. Wait for registry sync                                         │
│  3. Query registry: find specialist's registry resource name       │
│  4. Pass SPECIALIST_REGISTRY_NAME as env var to orchestrator       │
│  5. Deploy orchestrator with --agent-identity                      │
│                                                                    │
│  RUNTIME (orchestrator agent.py):                                  │
│                                                                    │
│  1. Read SPECIALIST_REGISTRY_NAME from env                         │
│  2. Initialize AgentRegistry client                                │
│  3. Call registry.get_remote_a2a_agent(name, httpx_client)         │
│     ├── Registry API returns agent metadata                        │
│     ├── Extracts A2A card URL from protocols[0].interfaces[0].url  │
│     └── Creates RemoteA2aAgent with card URL                       │
│  4. Add RemoteA2aAgent as sub_agent                                │
│  5. User query → orchestrator → A2A call → specialist → response   │
│                                                                    │
│  FALLBACK:                                                         │
│                                                                    │
│  If registry discovery fails:                                      │
│  1. Read SPECIALIST_A2A_CARD_URL from env                          │
│  2. Create RemoteA2aAgent directly with card URL                   │
│  3. Same pattern as a2a-demo                                       │
└────────────────────────────────────────────────────────────────────┘
```

---

## Authentication Between Agents

### With Default SA (a2a-demo pattern)

```python
class _GCPAuth(httpx.Auth):
    def __init__(self):
        self._credentials, _ = google.auth.default()

    def auth_flow(self, request):
        self._credentials.refresh(google.auth.transport.requests.Request())
        request.headers["Authorization"] = f"Bearer {self._credentials.token}"
        yield request
```

This pattern works for both SA and SPIFFE deployments because `google.auth.default()` returns the active credentials, which are automatically SPIFFE-bound when deployed with `--agent-identity`.

### What Changes with SPIFFE

When agents have SPIFFE identities:
- **Tokens are certificate-bound** — cannot be replayed from outside the agent's runtime environment
- **DPoP (Demonstrating Proof of Possession)** — tokens include a cryptographic proof that the caller possesses the private key
- **Audit trails** — Cloud Audit Logs show the specific agent's SPIFFE ID, not a shared SA
- **Per-agent IAM** — you can grant permissions to individual agents, not all agents in the project

The `_GCPAuth` class works unchanged because the credential binding happens at the platform level, not in application code.

---

## Stumbling Blocks & Solutions

### 1. `AgentRegistry` Requires the `agent-identity` Extra

**Problem:** Importing `google.adk.integrations.agent_registry` at runtime fails with:
```
ModuleNotFoundError: No module named 'google.cloud.iamconnectorcredentials_v1alpha'
```

**Root cause:** `AgentRegistry` imports `agent_identity` internally, which depends on `google-cloud-iamconnectorcredentials`. This package is only installed via the `google-adk[agent-identity]` extra.

**Solution:** Add both extras to `pyproject.toml`:
```toml
"google-adk[a2a,agent-identity]>=1.27.0,<2.0.0"
```

The `a2a` extra provides `RemoteA2aAgent` and `a2a-sdk`, while `agent-identity` provides the IAM connector credentials needed by `AgentRegistry`.

### 2. `effectiveIdentity` Not Returned Immediately via API

**Problem:** After deploying with `--agent-identity`, querying the Reasoning Engine API for `effectiveIdentity` returns empty for several minutes.

**Discovery:** The SPIFFE identity IS provisioned — it appears in Agent Registry's `RuntimeIdentity` field almost immediately. The `effectiveIdentity` field in the Reasoning Engine API response takes longer to populate.

**Solution:** Use Agent Registry to verify SPIFFE identity instead of the RE API:
```bash
gcloud alpha agent-registry agents list --location=us-central1 \
    --filter="displayName='currency_specialist'" --format=yaml
# Look for: RuntimeIdentity.principal: principal://agents.global.org-...
```

### 3. `agents-cli deploy` Display Name Comes from pyproject.toml

**Problem:** The `SPECIALIST_DISPLAY_NAME` env var in deploy.sh has no effect on the deployed agent name. `agents-cli deploy` uses the `name` field from `[tool.agents-cli]` in `pyproject.toml`.

**Solution:** Set distinct names in each demo's `pyproject.toml` `[tool.agents-cli]` section:
```toml
[tool.agents-cli]
name = "spiffe-specialist"  # NOT "specialist-agent"
```

### 4. CAA Token-Binding Blocks A2A Calls Without Agent Gateway

**Problem:** With SPIFFE identity, the orchestrator's A2A call to fetch the specialist's agent card returns **403 Forbidden**. The call from `RemoteA2aAgent` uses `_GCPAuth(httpx.Auth)` with `google.auth.default()` credentials, but with SPIFFE these tokens are **certificate-bound** (DPoP). The specialist's Agent Runtime rejects the token because the call arrives via plain HTTP, not mTLS.

**Error in logs:**
```
AgentCardResolutionError: Failed to resolve AgentCard from URL .../a2a/v1/card:
HTTP Error 403: Client error '403 Forbidden'
```

**Root cause:** Context-Aware Access (CAA) enforces that certificate-bound tokens can only be used via mTLS connections. The `httpx.AsyncClient` in `_GCPAuth` makes plain HTTPS calls, not mTLS. Without Agent Gateway (which handles mTLS termination), the token can't be validated.

**Solution (workaround):** Disable CAA token-binding:
```bash
agents-cli deploy --agent-identity \
    --update-env-vars "GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False,..."
```

**Production solution:** Use Agent Gateway for agent-to-agent routing, which handles mTLS between SPIFFE-enabled agents. The workaround above is acceptable for demos and development but reduces security (tokens become replayable).

### 5. SPIFFE Agents Need Extensive IAM Grants

**Problem:** After deploying with `--agent-identity`, the agent only gets two auto-granted roles: `agentDefaultAccess` + `agentContextEditor`. These are insufficient for:
- Calling the Gemini API (needs `aiplatform.user`)
- Accessing Agent Registry (needs `cloudapiregistry.viewer`)
- Writing Cloud Logging (needs `logging.logWriter`)
- Calling other Agent Runtime instances (needs `aiplatform.user`)

**Solution:** Grant baseline roles to all agents via **principal set** before deploying:
```bash
PRINCIPAL_SET="principalSet://agents.global.org-{ORG_ID}.system.id.goog/attribute.platformContainer/aiplatform/projects/{PROJECT_NUMBER}"

for ROLE in roles/aiplatform.user roles/serviceusage.serviceUsageConsumer \
    roles/logging.logWriter roles/monitoring.metricWriter \
    roles/cloudapiregistry.viewer roles/storage.objectAdmin; do
    gcloud projects add-iam-policy-binding PROJECT_ID \
        --member="${PRINCIPAL_SET}" --role="${ROLE}" --condition=None --quiet
done
```

IAM propagation takes 1-2 minutes. Grant roles **before** deploying agents to avoid startup failures.

### 6. Shared Agent RE IDs Across Demos

**Finding:** If two demos share the same `[tool.agents-cli] name`, `agents-cli deploy` updates the existing agent instead of creating a new one. The a2a-demo and spiffe-registry-demo originally shared the name "specialist-agent", causing deploys to overwrite each other.

**Solution:** Use unique names per demo in `pyproject.toml`.

---

## Deployment Sequence

```
deploy.sh
  │
  ├── Step 1: Create GCS staging bucket
  │
  ├── Step 2: Deploy Specialist (A2A + SPIFFE)
  │   ├── agents-cli deploy --agent-identity
  │   ├── Extract SPIFFE ID from API (with retry)
  │   └── Writes specialist-agent/deployment_metadata.json
  │
  ├── Step 3: Verify Specialist in Agent Registry
  │   ├── Wait for registry sync (~15s)
  │   ├── Query registry for specialist by displayName
  │   ├── Verify type=A2A_AGENT, skills populated
  │   ├── Check RuntimeIdentity (SA vs SPIFFE)
  │   └── Extract SPECIALIST_REGISTRY_NAME
  │
  ├── Step 4: Deploy Orchestrator (ADK + SPIFFE)
  │   ├── Pass SPECIALIST_REGISTRY_NAME as env var
  │   ├── Pass SPECIALIST_A2A_CARD_URL as fallback
  │   ├── agents-cli deploy --agent-identity
  │   └── Extract orchestrator SPIFFE ID
  │
  ├── Step 5: Verify both agents in Agent Registry
  │   └── List all project agents, show SPIFFE principals
  │
  └── Step 6: Optional GE registration

undeploy.sh (reverse order)
  │
  ├── Step 1: Delete Orchestrator
  ├── Step 2: Delete Specialist
  └── Step 3: Delete staging bucket
```

---

## Design Decisions

### Why `AgentRegistry.get_remote_a2a_agent()` (not `list_agents` + manual construction)?

The `get_remote_a2a_agent()` method handles the full lifecycle:
1. Fetches agent metadata from registry
2. Extracts the A2A card URL
3. Creates a properly configured `RemoteA2aAgent`

Using `list_agents()` + manual URL extraction would duplicate this logic and miss any future improvements in the ADK integration.

### Why pass registry name via env var (not discover at runtime)?

The orchestrator needs to know *which* agent to connect to. While `list_agents()` could search by displayName or skills, the registry name is deterministic and avoids ambiguity (multiple agents could match a search query). The env var approach also survives registry API outages at startup.

### Why keep the URL fallback?

The `SPECIALIST_A2A_CARD_URL` fallback ensures the demo works even if:
- Agent Registry API is temporarily unavailable
- Registry sync is delayed
- The `get_remote_a2a_agent()` method has a bug
- The demo is run in a project without Agent Registry enabled

### Why the same business logic as a2a-demo?

The demo's purpose is to exercise SPIFFE + Registry mechanics, not business logic. Reusing the currency conversion specialist keeps the focus on the infrastructure differences and makes it easy to compare the two approaches.
