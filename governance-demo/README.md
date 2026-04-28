# Google Agent Platform Governance Demo

Demonstrates how to build and govern agents on Google Cloud's Agent Platform using the **Agent Developer Kit (ADK)**, **Reasoning Engine (Agent Runtime)**, **Agent Registry**, **Agent Gateway**, and **IAM Deny Policies**.

## Architecture

1. **MCP Server**: A Python `mcp.server.Server` using Starlette deployed to **Cloud Run**. It exposes an SSE transport endpoint and implements two tools:
    * `get_account_balance` (read-only)
    * `transfer_funds` (destructive/write-operation)
2. **Agent Registry**: The central catalog where the MCP server is registered, mapping the endpoint URL and attaching metadata (the Tool Spec) to each exposed tool. The ADK agent uses `AgentRegistry.get_mcp_toolset()` for **runtime endpoint discovery** instead of hardcoding URLs.
3. **Agent Gateway**: A managed `AGENT_TO_ANYWHERE` networking and security gateway. It intercepts requests going from the Reasoning Engine to external or internal tools.
4. **ADK Agent**: A simple LLM agent deployed into **Reasoning Engine** (Agent Runtime). The deployment configures the agent to route its MCP traffic through the Agent Gateway using `agent_gateway_config`.
5. **IAM Deny Policy**: The cornerstone of the governance. A project-level deny policy ensures that the agent is restricted to only executing tools marked as read-only.

---

## Key Learnings & Pitfalls

### 1. Cloud Run and MCP Server-Sent Events (SSE)
When deploying a Python-based MCP server to Cloud Run, `FastMCP` abstraction can be difficult to bind appropriately to Cloud Run's required `$PORT` when using HTTP/SSE.
**Solution:** We use a lower-level `mcp.server.Server` implementation wrapped in a `starlette` application, explicitly handling the `/sse` and `/messages` endpoints.

### 2. Agent Registry Tool Annotations (`toolspec.json`)
By default, the Agent Gateway doesn't inherently know if a tool is safe or destructive. We register the service in the Agent Registry using `gcloud alpha agent-registry services create` and supply an explicit `toolspec.json`:
* `readOnlyHint`: Boolean. Indicates the tool is safe and reads data.
* `destructiveHint`: Boolean. Indicates the tool modifies state or data.

### 3. Agent Registry for Endpoint Discovery
Instead of hardcoding MCP server URLs, the agent uses `AgentRegistry.get_mcp_toolset()` to resolve the MCP server endpoint at runtime. This decouples the agent from infrastructure details and enables centralized endpoint management.

### 4. Agent Gateway Attachment & Python SDK
The `google-cloud-aiplatform` SDK (`ReasoningEngine.create()`) supports attaching the Agent Gateway via the `config` dictionary:
```python
config={
    "agent_gateway_config": {
        "agent_to_anywhere_config": {"agent_gateway": gateway_id}
    },
    "identity_type": 1, # AGENT_IDENTITY (SPIFFE)
}
```

### 5. MCP Governance via IAM (The Deny Policy Approach)
Agent Gateway tool governance uses **IAM Deny Policies** to evaluate tool execution based on the `mcp.googleapis.com/tool.isReadOnly` attribute:
```json
{
  "rules": [{
    "denyRule": {
      "deniedPrincipals": ["principalSet://goog/public:all"],
      "deniedPermissions": ["mcp.googleapis.com/tools.call"],
      "denialCondition": {
        "title": "Deny read-write tools",
        "expression": "api.getAttribute('mcp.googleapis.com/tool.isReadOnly', false) == false"
      }
    }
  }]
}
```

### 6. Deny Admin Role Requirements
To create IAM Deny Policies, the executing principal **must** possess `roles/iam.denyAdmin` at the Organization or Folder level. Project Owner or Organization Administrator is **not sufficient**.

### 7. Observability and Telemetry
The Reasoning Engine service account (`service-PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) requires `roles/cloudtrace.agent` for Cloud Trace. The deploy script grants this automatically.

---

## Prerequisites

* `gcloud` CLI authenticated with appropriate permissions
* `uv` package manager installed, `agents-cli` installed (`uv tool install google-agents-cli`)
* `envsubst` available (standard on Linux/macOS)
* **Agent Gateway allowlist**: Your project must be allowlisted for the "Agent Gateway for Agent Engine" integration. Without this, attaching a gateway to a Reasoning Engine returns `FAILED_PRECONDITION: Agent Gateway is not enabled for this project`. Request access from the Agent Platform team. (This requirement will likely be removed when the feature reaches GA.)
* **One gateway per region per type**: Only one `AGENT_TO_ANYWHERE` gateway can exist per project+region. Having two causes the same `FAILED_PRECONDITION` error.

## Executing the Demo

### Option A: Without Agent Gateway (works today)

Deploy the agent without gateway routing. Both tools (read + write) will succeed — no governance enforcement.

1. Copy the environment template and fill in your values:
   ```bash
   cp .env.template .env
   ```

2. Deploy infrastructure (MCP server, bucket, registry, gateway, IAM roles):
   ```bash
   ./deploy.sh
   ```

3. Deploy the agent via `agents-cli`:
   ```bash
   cd demo-agent
   agents-cli deploy --project PROJECT_ID --region us-central1 --agent-identity \
     --update-env-vars "MCP_SERVER_URL=https://MCP_URL/sse,GEMINI_MODEL=gemini-3-flash-preview,LOGS_BUCKET_NAME=PROJECT_ID-agent-staging,OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT,GOOGLE_CLOUD_LOCATION=global" \
     --no-confirm-project
   ```

4. Test:
   ```bash
   agents-cli run "Check my account balance for user123"
   ```

### Option B: With Agent Gateway (requires allowlist)

Deploy with gateway routing and IAP policy enforcement. Write tools will be blocked.

1. Complete steps 1-2 from Option A.

2. Deploy the agent using `deploy_agent.py` (single-call create with gateway):
   ```bash
   cd demo-agent
   PROJECT_ID=your-project REGION=us-central1 \
     MCP_SERVER_URL=https://MCP_URL/sse \
     AGENT_GATEWAY_RESOURCE_ID=projects/your-project/locations/us-central1/agentGateways/your-gateway \
     uv run python deploy_agent.py
   ```
   Note: `agents-cli deploy` does not support `agent_gateway_config` — use `deploy_agent.py` for gateway deployments. The gateway **must** be attached at creation time (cannot be added to an existing engine, cannot be unbound).

3. Apply IAP policies for tool governance.

4. Test — `get_account_balance` should succeed, `transfer_funds` should be blocked.

### Cleanup

```bash
./undeploy.sh
```

## Demo Flow

* **Test 1 (Allowed):** Prompt the agent to "Check my account balance". The agent invokes `get_account_balance` successfully.
* **Test 2 (Blocked, Option B only):** Prompt the agent to "Transfer $500 to John". The Agent Gateway blocks the request due to IAP policy. The agent reports the inability to complete the transaction.

## Known Limitations (April 2026)

* **Agent Gateway allowlist required**: The "Agent Gateway for Agent Engine" integration requires project-level allowlisting. The networking-level Agent Gateway resource can be created freely, but attaching it to a Reasoning Engine requires backend enablement.
* **`agents-cli deploy` does not support gateway config**: Use `deploy_agent.py` for gateway deployments. `agents-cli` creates a shell agent first (identity only), then updates with code — the gateway config is silently dropped during the update step.
* **Agent Registry auth from Reasoning Engine**: `AgentRegistry.get_mcp_toolset()` returns 401 inside Reasoning Engine. Use `MCP_SERVER_URL` with direct `SseConnectionParams` as fallback until resolved.
* **Authz policies on Google-managed gateways**: `gcloud beta network-security authz-policies import` rejects all `loadBalancingScheme` values for Google-managed gateways. Use IAP Allow Policies instead.
* **Deny policies may trigger org violations**: IAM Deny Policies using `principalSet://goog/public:all` can trigger org-level policy violations in managed environments (e.g., Argolis).

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
