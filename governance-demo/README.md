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
* `roles/iam.denyAdmin` granted at the Organization level
* `uv` package manager installed
* `envsubst` available (standard on Linux/macOS)

## Executing the Demo

1. Copy the environment template and fill in your values:
   ```bash
   cp .env.template .env
   # Edit .env with your PROJECT_ID and preferences
   ```

2. Run the deployment script:
   ```bash
   ./deploy.sh
   ```

3. Test your agent (read-only should work, write should be denied):
   ```bash
   cd demo-agent
   PROJECT_ID=your-project REGION=us-central1 uv run python test_deployed_agent.py
   ```

4. Clean up all resources:
   ```bash
   ./undeploy.sh
   ```

## Demo Flow

* **Test 1 (Allowed):** Prompt the agent to "Check my account balance". The agent invokes `get_account_balance` successfully.
* **Test 2 (Blocked):** Prompt the agent to "Transfer $500 to John". The Agent Gateway blocks the request due to the IAM read-only deny policy. The agent reports the inability to complete the transaction.

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
| `DENY_POLICY_NAME` | `mcp-read-only-policy` | IAM deny policy name |
