# Google Agent Platform Governance Demo

Demonstrates how to build and govern agents on Google Cloud's Agent Platform using the **Agent Developer Kit (ADK)**, **Reasoning Engine (Agent Runtime)**, **Agent Registry**, **Agent Gateway**, and **Authorization Policies**.

## Architecture

1. **MCP Server**: A Python `mcp.server.Server` using Starlette deployed to **Cloud Run**. It exposes a Streamable HTTP transport endpoint (`/mcp`) and implements two tools:
    * `get_account_balance` (read-only)
    * `transfer_funds` (destructive/write-operation)
2. **Agent Registry**: The central catalog where the MCP server is registered, mapping the endpoint URL and attaching metadata (the Tool Spec) to each exposed tool. The ADK agent uses `AgentRegistry.get_mcp_toolset()` for **runtime endpoint discovery** instead of hardcoding URLs.
3. **Agent Gateway**: A managed `AGENT_TO_ANYWHERE` networking and security gateway. It intercepts requests going from the Reasoning Engine to external or internal tools. Default-deny — all tool access blocked unless explicitly allowed.
4. **ADK Agent**: A simple LLM agent deployed into **Reasoning Engine** (Agent Runtime). The deployment configures the agent to route its MCP traffic through the Agent Gateway using `agentGatewayConfig`.
5. **Authorization Policies**: Tool governance via `gcloud beta network-security authz-policies` with MCP tool-name matching. An ALLOW policy grants access to specific tools (`get_account_balance`), while everything else is blocked by default.

---

## Key Learnings & Pitfalls

### 1. Cloud Run and MCP Streamable HTTP
When deploying a Python-based MCP server to Cloud Run, `FastMCP` abstraction can be difficult to bind appropriately to Cloud Run's required `$PORT`.
**Solution:** We use a lower-level `mcp.server.Server` with `StreamableHTTPSessionManager(stateless=True)` wrapped in a `starlette` application, serving a single `/mcp` endpoint. Stateless mode avoids session affinity issues on Cloud Run.

### 2. Agent Registry Tool Annotations (`toolspec.json`)
By default, the Agent Gateway doesn't inherently know if a tool is safe or destructive. We register the service in the Agent Registry using `gcloud alpha agent-registry services create` and supply an explicit `toolspec.json`:
* `readOnlyHint`: Boolean. Indicates the tool is safe and reads data.
* `destructiveHint`: Boolean. Indicates the tool modifies state or data.

### 3. Agent Registry for Endpoint Discovery
Instead of hardcoding MCP server URLs, the agent uses `AgentRegistry.get_mcp_toolset()` to resolve the MCP server endpoint at runtime. This decouples the agent from infrastructure details and enables centralized endpoint management.

### 4. Agent Gateway Attachment & Python SDK
The `vertexai.Client` (v1beta1) supports attaching the Agent Gateway at creation time via `AgentEngineConfig`:
```python
import vertexai
from vertexai._genai.types.common import AgentEngineConfig, IdentityType

client = vertexai.Client(project=project_id, location=location,
                         http_options={"api_version": "v1beta1"})

config = AgentEngineConfig(
    displayName="my-agent",
    identityType=IdentityType.AGENT_IDENTITY,
    agentGatewayConfig={
        "agentToAnywhereConfig": {"agentGateway": gateway_resource_id}
    },
)

agent = client.agent_engines.create(agent=agent_runtime, config=config)
```
See `demo-agent/deploy_agent.py` for the full working implementation. **Note:** `agents-cli deploy` silently drops gateway config — use `deploy_agent.py` instead.

### 5. Tool Governance via Authorization Policies
The Agent Gateway is **default-deny**: all tool access is blocked unless explicitly allowed. Governance uses **authorization policies** with MCP tool-name matching:

```yaml
# authz-allow-readonly.yaml.template
name: allow-readonly-tools
target:
  resources:
    - "projects/${PROJECT_ID}/locations/${REGION}/agentGateways/${GATEWAY_NAME}"
policyProfile: REQUEST_AUTHZ
httpRules:
  - to:
      operations:
        - mcp:
            baseProtocolMethodsOption: MATCH_BASE_PROTOCOL_METHODS
            methods:
              - name: "tools/list"
              - name: "tools/call"
                params:
                  - exact: "get_account_balance"
action: ALLOW
```

Applied via:
```bash
gcloud beta network-security authz-policies import allow-readonly-tools \
    --source=authz-allow-readonly.yaml --location=REGION
```

`baseProtocolMethodsOption: MATCH_BASE_PROTOCOL_METHODS` is required so the gateway doesn't break MCP session establishment.

### 6. IAP Delegation via Service Extensions
Authorization policies delegate to IAP via a Service Extension:
```yaml
name: iap-authz-ext
service: iap.googleapis.com
failOpen: true
```

This tells the gateway to use IAP for identity-based authorization decisions.

### 7. Observability and Telemetry
The Reasoning Engine service account (`service-PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) requires `roles/cloudtrace.agent` for Cloud Trace. The deploy script grants this automatically.

---

## Prerequisites

* `gcloud` CLI authenticated with appropriate permissions
* `uv` package manager installed, `agents-cli` installed (`uv tool install google-agents-cli`)
* `envsubst` available (standard on Linux/macOS)
* **Agent Gateway allowlist**: Your project must be allowlisted for the "Agent Gateway for Agent Engine" integration. Without this, attaching a gateway to a Reasoning Engine returns `FAILED_PRECONDITION: Agent Gateway is not enabled for this project`. (This requirement will likely be removed when the feature reaches GA.)
* **One gateway per region per type**: Only one `AGENT_TO_ANYWHERE` gateway can exist per project+region.

## Quick Start

1. Copy the environment template and fill in your project ID:
   ```bash
   cp .env.template .env
   # Edit .env — set PROJECT_ID
   ```

2. Deploy everything (MCP server, registry, gateway, agent, authz policies):
   ```bash
   ./deploy.sh
   ```

3. Test:
   ```bash
   cd demo-agent
   agents-cli run --url $(python3 -c "import json; print(json.load(open('deployment_metadata.json'))['remote_agent_runtime_id'])") \
     --mode adk 'Check my account balance for user123'
   ```

### Cleanup

```bash
./undeploy.sh
```

## Demo Flow

* **Test 1 (Allowed):** Prompt the agent to "Check my account balance". The agent invokes `get_account_balance` — allowed by the authz policy.
* **Test 2 (Blocked):** Prompt the agent to "Transfer $500 to John". The Agent Gateway blocks the request (not in the ALLOW list). The agent reports the inability to complete the transaction.

## Known Limitations (May 2026)

* **Agent Gateway allowlist required**: The "Agent Gateway for Agent Engine" integration requires project-level allowlisting.
* **`agents-cli deploy` does not support gateway config**: Use `deploy_agent.py` for gateway deployments.
* **Gateway is creation-time only**: Cannot add/remove gateway from an existing Agent Engine. Must delete and recreate.
* **`_LazyToolset` wrapper required**: Agent Runtime imports the agent module during health checks. Registry calls during import fail, so tools must be lazily initialized.
* **MCP server is unauthenticated**: Deployed with `--allow-unauthenticated` for demo simplicity.

See [GAPS.md](GAPS.md) for the full breakdown.

## Configuration

All configuration is managed through the `.env` file (see `.env.template`):

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT_ID` | auto-detected | GCP project ID |
| `REGION` | `us-central1` | GCP region |
| `GATEWAY_NAME` | `demo-agent-gateway` | Agent Gateway name |
| `MCP_SERVICE_NAME` | `finance-mcp-server` | Cloud Run service name |
| `AGENT_REGISTRY_SERVICE_NAME` | `finance-mcp-service` | Agent Registry entry |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Gemini model for the agent |
