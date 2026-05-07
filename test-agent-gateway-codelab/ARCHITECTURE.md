# Agent Gateway Codelab — Architecture Deep Dive

## What This Codelab Does

Deploys a **mortgage underwriting assistant** (ADK agent) to Vertex AI Agent Runtime that connects to three internal MCP servers through an **Agent Gateway**. Every request is governed by **IAP** (identity-based access control) and **Model Armor** (content safety screening). Tools are discovered dynamically via the **Agent Registry**.

---

## End-to-End Architecture

```
 Users
   |
   v
+------------------+      +------------------+      +-------------------+
|                  |      |                  |      |                   |
| Gemini           |----->| Agent Gateway    |----->| Agent Runtime     |
| Enterprise       |      | (inbound)        |      | (Reasoning Engine)|
|                  |      |                  |      |                   |
| Orchestrator     |      | Auth + routing   |      | Mortgage          |
| Agent            |      | for user traffic |      | Assistant Agent   |
+------------------+      +------------------+      +--------+----------+
                                                             |
                          +----------------------------------+
                          |
                          v
               +---------------------+
               |   Agent Gateway     |
               |   (outbound)        |
               |   AGENT_TO_ANYWHERE |
               +----------+----------+
                          |
          +---------------+----------------+
          |               |                |
          v               v                v
  +-------+------+ +-----+--------+ +-----+--------+
  | REQUEST_AUTHZ| |CONTENT_AUTHZ | | Agent        |
  | (IAP)        | |(Model Armor) | | Registry     |
  |              | |              | |              |
  | Per-MCP      | | RAI filters  | | Discovers    |
  | iap.egressor | | PI/jailbreak | | MCP servers  |
  | bindings     | | SDP (DLP)    | | + Google APIs|
  | + CEL conds  | | Malicious URI| |              |
  +--------------+ +--------------+ +--------------+
                          |
          +---------------+----------------+
          |               |                |
          v               v                v
  +-------+------+ +-----+--------+ +-----+--------+
  | legacy-dms   | | income-      | | corporate-   |
  | (Cloud Run)  | | verification | | email        |
  |              | | (Cloud Run)  | | (Cloud Run)  |
  | search_docs  | |              | |              |
  | get_document | | verify_      | | send_email   |
  | (read-only)  | | applicant    | | read_email   |
  |              | | (read-only)  | | (destructive)|
  +--------------+ +--------------+ +--------------+
```

---

## Component Details

### 1. Mortgage Assistant Agent (ADK)

**Location:** `src/mortgage-agent/agent/agent.py`

An ADK agent deployed to Vertex AI Agent Runtime (Reasoning Engine). Key design:

- **Pickle-safe**: Custom `_PickleSafeAgent` with `__reduce__`/`__deepcopy__` — rebuilds itself with fresh MCP tool discovery when unpickled on Agent Engine
- **Dynamic tool discovery**: No hardcoded MCP URLs. At startup, queries Agent Registry for all registered MCP servers and builds ADK toolsets
- **Error handling**: Custom `on_tool_error_callback` catches 403s from IAP and returns a user-friendly denial message
- **Model**: `gemini-3.1-flash-lite-preview` (configurable)

**Env vars controlling discovery:**
```
MCP_REGISTRY_PROJECT   → which project to query
MCP_REGISTRY_LOCATION  → which region (must be a real region, not "global")
MCP_REGISTRY_FILTER    → optional list-filter expression
MCP_REGISTRY_ENDPOINT  → optional base URL override
```

### 2. MCP Servers (FastMCP on Cloud Run)

Three Python MCP servers, each deployed as a Cloud Run service:

```
+---------------------------+-----------------------------+------------------+
| Service                   | Tools                       | Annotations      |
+---------------------------+-----------------------------+------------------+
| legacy-dms                | search_documents            | read-only        |
|                           | get_document                | read-only        |
+---------------------------+-----------------------------+------------------+
| income-verification-api   | verify_applicant            | read-only        |
+---------------------------+-----------------------------+------------------+
| corporate-email           | read_email                  | read-only        |
|                           | send_email                  | DESTRUCTIVE      |
+---------------------------+-----------------------------+------------------+
```

Each exposes `/mcp` (Streamable HTTP) and `/health` endpoints. Built with FastMCP + Starlette.

**Mock data**: Tax returns for "Sterling" applicants, employment records for "Julian"/"Elena" Sterling, sample corporate emails.

### 3. Agent Gateway

**Resource:** `google_network_services_agent_gateway`
**Mode:** `AGENT_TO_ANYWHERE` (intercepts ALL outbound traffic from the agent)
**Protocol:** MCP

The gateway is Google-managed infrastructure that:
1. Intercepts all outbound HTTP/gRPC from the Agent Runtime
2. Rewrites URLs to `*.mtls.googleapis.com` for mTLS
3. Resolves destinations against the Agent Registry
4. Evaluates authz extensions (IAP, Model Armor) before forwarding
5. Logs every request with agent identity

**Networking:**
```
Agent Runtime
    |
    v
PSC-Interface Network Attachment
    |   (agent_gateway_subnet, e.g., 10.20.0.0/28)
    v
Agent Gateway (Google-managed)
    |
    +---> Google APIs (via mTLS endpoints)
    |
    +---> MCP Servers (via Cloud Run URLs or internal LB)
```

### 4. IAP REQUEST_AUTHZ (Identity-Based Access Control)

**Extension:** `agent-gateway-{name}-iap-authz`
**Service:** `iap.googleapis.com`
**Profile:** `REQUEST_AUTHZ` (evaluated once per request at headers stage)

**How it works:**
```
Request arrives at gateway
    |
    v
IAP evaluates: Does the agent's SPIFFE identity have
               roles/iap.egressor on the target resource?
    |
    +---> YES: Allow (request proceeds to Model Armor / destination)
    |
    +---> NO:  Deny (403 returned to agent)
    |
    +---> DRY_RUN: Log the decision but allow regardless
```

**Per-resource bindings** are set via `scripts/grant_agent_mcp_egress.sh` after agent deployment:
```bash
# Grant a specific agent access to specific MCP servers
./scripts/grant_agent_mcp_egress.sh \
  --agent-id ${AGENT_ID} \
  --mcp \
  --mcp-filter "legacy-dms income-verification"

# Grant with CEL condition (read-only tools only)
./scripts/grant_agent_mcp_egress.sh \
  --agent-id ${AGENT_ID} \
  --mcp \
  --condition-expression "api.getAttribute('iap.googleapis.com/mcp.tool.isReadOnly', false) == true" \
  --condition-title "read-only-tools-only"

# Grant ALL agents in the project (principalSet)
./scripts/grant_agent_mcp_egress.sh \
  --bind-all-agents \
  --mcp --endpoints
```

**IAP resource hierarchy:**
```
projects/{P}/locations/{R}/iap_web/agentRegistry/
    ├── mcpServers/{auto-generated-id}     ← per-MCP-server bindings
    └── endpoints/{auto-generated-id}       ← per-endpoint bindings (Google APIs)
```

### 5. Model Armor CONTENT_AUTHZ (Content Safety)

**Extension:** `agent-gateway-{name}-ma-authz`
**Service:** `modelarmor.{region}.rep.googleapis.com`
**Profile:** `CONTENT_AUTHZ` (streams request/response body for inspection)

**Request screening:**
- Responsible AI (RAI): hate speech, harassment, sexually explicit content
- Prompt injection / jailbreak detection
- Malicious URI detection

**Response screening:**
- Same RAI filters as request
- Sensitive Data Protection (SDP) via Cloud DLP:
  - Detects: SSN, credit card, phone, email, passport, DOB, medical records
  - Action: Replace with info-type placeholders (e.g., `[US_SOCIAL_SECURITY_NUMBER]`)

**Scoping:**
- Can be scoped to specific Host headers (only scan MCP traffic, not Google API traffic)
- Floor settings enforce minimum protection at the project level

### 6. Agent Registry

**58 registered entries** covering:

| Category | Count | Examples |
|----------|-------|---------|
| Google APIs (5 variants each) | ~55 | `aiplatform`, `aiplatform-mtls`, `us-central1-aiplatform`, etc. |
| MCP servers | 3 | `legacy-dms`, `income-verification`, `corporate-email` |
| Custom services | 1 | `github` |

Each Google API is registered in 5 variants to handle URL rewriting:
```
{id}.googleapis.com                        (base)
{id}.mtls.googleapis.com                   (mTLS — gateway rewrites to this)
{region}-{id}.googleapis.com               (locational)
{region}-{id}.mtls.googleapis.com          (locational mTLS)
{id}.{region}.rep.googleapis.com           (regional REP)
```

This ensures the gateway can resolve ANY outbound URL to a registered entry, preventing the `unregisteredEndpoint` problem.

---

## Deployment Modes

### Default (Public) Mode
```
enable_cloud_run_private_networking = false
```

```
Agent Runtime ---> Agent Gateway ---> Cloud Run (*.run.app, ingress=all)
                        |
                        +---> Google APIs (*.mtls.googleapis.com)
```

- MCP servers reachable at public `*.run.app` URLs
- No internal LB, no private DNS, no certificates
- Simplest setup

### Secure (Private) Mode
```
enable_cloud_run_private_networking = true
enable_certificate_manager = true
```

```
Agent Runtime ---> Agent Gateway ---> PSC-I ---> Internal ALB ---> Cloud Run
                        |                            |            (internal-only)
                        |                     URL-mask NEG
                        |                     Host: legacy-dms.mcp.example.com
                        |
                        +---> DNS Peering ---> Private DNS Zone
                                               mcp.example.com -> LB VIP
```

- MCP servers internal-only (no public access)
- Internal Application LB with Google-managed TLS cert
- Private DNS zone for hostname resolution
- Agent Gateway DNS peering resolves hostnames through VPC

---

## Governance Chain — Request Lifecycle

```
1. Agent calls tool (e.g., dms_search_documents)
   |
2. ADK resolves MCP server URL from Agent Registry
   |  URL: https://legacy-dms-hash-uc.a.run.app/mcp
   |
3. Gateway intercepts outbound request
   |  Rewrites to: https://legacy-dms-hash-uc.a.run.app.mtls/mcp
   |  Resolves against Agent Registry -> mcpServer "legacy-dms"
   |
4. REQUEST_AUTHZ (IAP) evaluates:
   |  Principal: principal://agents.global.org-{ORG}/resources/.../reasoningEngines/{ID}
   |  Resource:  projects/{P}/locations/global/iap_web/agentRegistry/mcpServers/{auto-id}
   |  Permission: iap.webServiceVersions.egressViaIAP
   |  Condition:  (optional CEL, e.g., mcp.tool.isReadOnly == true)
   |
   |  DRY_RUN mode: logs decision, allows regardless
   |  Enforcement mode: blocks if no binding exists
   |
5. CONTENT_AUTHZ (Model Armor) screens request body:
   |  RAI filters, PI/jailbreak detection, malicious URI check
   |  If blocked -> 403 returned to agent
   |
6. Request forwarded to MCP server
   |
7. MCP server processes tool call, returns response
   |
8. CONTENT_AUTHZ (Model Armor) screens response body:
   |  RAI filters, SDP/DLP (replaces SSN -> [US_SOCIAL_SECURITY_NUMBER])
   |
9. Sanitized response returned to agent
   |
10. Agent presents results to user via Gemini
```

---

## Terraform Module Dependency Graph

```
foundation
    |
    +---> networking
    |         |
    |         +---> mcp_services (Cloud Run)
    |         |         |
    |         |         +---> mcp_internal_lb (conditional)
    |         |         |         |
    |         |         |         +---> agent_gateway (conditional)
    |         |         |
    |         |         +---> agent_registry_endpoints (conditional)
    |         |
    |         +---> certificates (conditional)
    |         |
    |         +---> dns (conditional)
    |
    +---> agent_engine (IAM for agent identity)
    |
    +---> model_armor (conditional)
              |
              +---> DLP templates (inspect + de-identify)
              +---> Request/response Model Armor templates
              +---> Floor settings (project-level MCP protection)
```

---

## Key Configuration Flags

| Flag | Default | What it enables |
|------|---------|-----------------|
| `enable_agent_gateway` | `true` | Agent Gateway + PSC-I + IAP + Model Armor authz |
| `enable_model_armor` | `true` | Model Armor templates, DLP, floor settings |
| `enable_agent_engine` | `true` | Agent identity IAM (principalSet grants) |
| `enable_agent_registry_endpoints` | `true` | Register Google APIs + MCP servers |
| `enable_cloud_run_private_networking` | `false` | Internal LB, private DNS, internal-only Cloud Run |
| `enable_certificate_manager` | `true` | Google-managed TLS cert for internal LB |
| `enable_psc_interface` | `false` | PSC Interface for Agent Runtime VPC access |

---

## Post-Deploy Steps

After Terraform + Skaffold + `deploy_agent.py`:

1. **Grant IAP egress** — Run `grant_agent_mcp_egress.sh` to give the agent's SPIFFE identity `roles/iap.egressor` on each MCP server
2. **Test in Playground** — Open the Agent Platform console, send a mortgage query
3. **Verify in Cloud Trace** — Check end-to-end spans (agent → gateway → MCP)
4. **Register in Gemini Enterprise** — Use `deploy_agent.py --ge-deploy` with OAuth credentials

---

## Key Learnings

1. **IAP DRY_RUN is essential for initial deployment** — Without it, IAP denies the agent's startup traffic (Resource Manager, AI Platform, telemetry) because no `iap.egressor` bindings exist yet. Set `metadata.iamEnforcementMode: DRY_RUN` on the IAP extension.

2. **All Google APIs must be registered as endpoints** — The gateway resolves every outbound URL against the Agent Registry. Unregistered URLs hit `unregisteredEndpoint` which can't have IAP policies.

3. **5 URL variants per Google API** — The gateway rewrites URLs to mTLS and locational forms. Register `base`, `mtls`, `locational`, `locational-mtls`, and `regional-rep` variants.

4. **Per-MCP `iap.egressor` bindings are out-of-band** — Not in Terraform. Run `grant_agent_mcp_egress.sh` after each agent deploy.

5. **CEL conditions enable tool-level governance** — e.g., `mcp.tool.isReadOnly == true` limits an agent to read-only tools only.

6. **Model Armor SDP sanitizes responses** — SSNs and PII in MCP responses are replaced with placeholders before reaching the agent.
