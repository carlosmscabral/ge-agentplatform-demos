# Google Agent Platform Governance Demo

This repository demonstrates how to build and govern agents on Google Cloud's Agent Platform using the **Agent Developer Kit (ADK)**, **Reasoning Engine (Agent Runtime)**, **Agent Registry**, **Agent Gateway**, and **Identity and Access Management (IAM) Deny Policies**.

## Architecture

1.  **MCP Server**: A Python `mcp.server.Server` using Starlette deployed to **Cloud Run**. It exposes a Server-Sent Events (SSE) transport endpoint and implements two tools:
    *   `get_account_balance` (read-only)
    *   `transfer_funds` (destructive/write-operation)
2.  **Agent Registry**: The central catalog where the MCP server is registered, mapping the endpoint URL and attaching metadata (the Tool Spec) to each exposed tool.
3.  **Agent Gateway**: A managed `AGENT_TO_ANYWHERE` networking and security gateway. It intercepts requests going from the Reasoning Engine to external or internal tools.
4.  **ADK Agent**: A simple LLM agent deployed into **Reasoning Engine** (Agent Runtime). The deployment configures the agent to route its MCP traffic through the Agent Gateway.
5.  **IAM Deny Policy**: The cornerstone of the governance. A project-level deny policy ensures that the agent is restricted to only executing tools marked as read-only.

---

## Key Learnings & Pitfalls

### 1. Cloud Run and MCP Server-Sent Events (SSE)
When deploying a Python-based MCP server to Cloud Run, `FastMCP` abstraction can be difficult to bind appropriately to Cloud Run's required `$PORT` when using HTTP/SSE.
**Solution:** We moved away from FastMCP towards a lower-level `mcp.server.Server` implementation wrapped in a `starlette` application, explicitly handling the `/sse` and `/messages` endpoints. This ensures robust and container-friendly transport bindings.

### 2. Agent Registry Tool Annotations (`toolspec.json`)
By default, the Agent Gateway doesn't inherently know if a tool is safe or destructive. We must manually register the service in the `Agent Registry` using `gcloud alpha agent-registry services create` and supply an explicit `toolspec.json` that outlines the tools.
**Crucial Attributes:**
*   `readOnlyHint`: Boolean. Indicates the tool is safe and reads data.
*   `destructiveHint`: Boolean. Indicates the tool modifies state or data.

### 3. Agent Gateway Attachment & Python SDK
The `google-cloud-aiplatform` SDK (`ReasoningEngine.create()`) currently has limitations on attaching the Agent Gateway programmatically in stable releases.
**Workaround:** We pass the undocumented configurations down via the `config` dictionary:
```python
config={
    "agent_gateway_config": {
        "agent_to_anywhere_config": {"agent_gateway": gateway_id}
    },
    "identity_type": 1, # AGENT_IDENTITY (SPIFFE)
}
```
*Note: Depending on the SDK version and environment, manual attachment of the Agent Gateway to the Reasoning Engine might still be necessary via the Google Cloud Console.*

### 4. MCP Governance via IAM (The Deny Policy approach)
The most significant finding. Agent Gateway tool governance **is NOT** handled via standard `roles/iap.egressor` Allow-Policy bindings or Network Security Authz policies.

Instead, the Google Cloud MCP security model uses **IAM Deny Policies** to evaluate tool execution based on the `mcp.googleapis.com/tool.isReadOnly` attribute.

**The Policy Rule:**
```json
{
  "rules": [
    {
      "denyRule": {
        "deniedPrincipals": ["principalSet://goog/public:all"],
        "deniedPermissions": ["mcp.googleapis.com/tools.call"],
        "denialCondition": {
          "title": "Deny read-write tools",
          "expression": "api.getAttribute('mcp.googleapis.com/tool.isReadOnly', false) == false"
        }
      }
    }
  ]
}
```
This policy asserts: *Deny the `mcp.googleapis.com/tools.call` permission to everyone if the `isReadOnly` attribute on the tool being called is `false`.*

### 5. Deny Admin Role Requirements
To create, update, or delete IAM Deny Policies, the executing principal (user or service account) **must** possess the `roles/iam.denyAdmin` role at the Organization or Folder level. Being a Project Owner or Organization Administrator is **not sufficient**. Google Cloud strictly isolates deny policies to prevent accidental lockouts.

### 6. Observability and Telemetry
When an ADK Agent is deployed to Reasoning Engine, it automatically tries to emit OpenTelemetry data to Cloud Trace. If the Reasoning Engine service account (`service-PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) lacks the `roles/cloudtrace.agent` role, you will see `401 Unauthorized` errors in the logs. Ensure this role is attached during setup.

---

## Executing the Demo

1. Ensure your user has `roles/iam.denyAdmin` granted at the Organization level.
2. Run the deployment script:
   ```bash
   ./deploy.sh
   ```
3. Test your agent. It should successfully fetch the account balance but receive a `PERMISSION_DENIED` error when attempting to transfer funds.
4. Clean up the resources:
   ```bash
   ./undeploy.sh
   ```