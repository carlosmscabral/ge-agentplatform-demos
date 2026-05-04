# Agent Platform Learnings

Hard-won knowledge from building governance demos on Google Agent Platform.
Most of this is undocumented or poorly documented as of April 2026.

---

## Agent Gateway

### gcloud CLI requires `alpha` track
All Agent Gateway and Agent Registry commands need `gcloud alpha`:
```bash
gcloud alpha network-services agent-gateways import ...
gcloud alpha agent-registry services create ...
gcloud alpha agent-registry mcp-servers list ...
```
The GA/beta tracks do not have these commands.

### Project allowlist required for Agent Engine integration
Attaching an Agent Gateway to a Reasoning Engine requires a **project-level allowlist** on the AI Platform backend. Without it, any attempt to include `agentGatewayConfig` in the create call returns:
```
400 FAILED_PRECONDITION: Agent Gateway is not enabled for this project.
```
This is separate from the networking-level Agent Gateway resource (which can be created freely). Request access from the Agent Platform team. This requirement will likely be removed at GA.

### One gateway per region per type
For a given project and region, there can only be **one** `AGENT_TO_ANYWHERE` (egress) and **one** `CLIENT_TO_AGENT` (ingress) gateway. Having two of the same type produces the same misleading `FAILED_PRECONDITION` error as the missing allowlist.

### Gateway must be attached at creation time
You cannot attach an Agent Gateway to an existing Reasoning Engine instance after creation. The `agentGatewayConfig` must be passed during `client.agent_engines.create()`. You also **cannot unbind** a gateway once attached.

### `agents-cli deploy` does not support gateway config
`agents-cli` uses a two-step flow: create identity shell → update with source code. The `agent_gateway_config` is silently dropped during the update step. **Use a standalone `deploy_agent.py`** that does a single `client.agent_engines.create()` with both source and gateway config. See `governance-demo/demo-agent/deploy_agent.py` for the working pattern.

### Gateway attachment uses the `vertexai.Client` (not `google.genai.Client`)
The correct client for Agent Engine operations with gateway config:
```python
import vertexai
from vertexai._genai.types.common import AgentEngineConfig, IdentityType

client = vertexai.Client(
    project=PROJECT_ID,
    location=REGION,
    http_options={"api_version": "v1beta1"},
)

agent = client.agent_engines.create(
    agent=agent_runtime,
    config=AgentEngineConfig(
        displayName="my-agent",
        identityType=IdentityType.AGENT_IDENTITY,
        agentGatewayConfig={
            "agentToAnywhereConfig": {
                "agentGateway": "projects/PROJECT/locations/REGION/agentGateways/GATEWAY"
            }
        },
        ...
    ),
)
```
Note: `google.genai.Client` does NOT have `agent_engines`. The `vertexai.Client` wraps it with the right API version.

### Authz policies don't work with Google-managed gateways (yet)
`gcloud beta network-security authz-policies import` requires `loadBalancingScheme` in the target, but no valid value (`INTERNAL_MANAGED`, `INTERNAL_SELF_MANAGED`, `EXTERNAL_MANAGED`) works for Google-managed Agent Gateways. The UG lists this as a roadmap item. Use IAP Allow Policies for tool-level governance instead (see IAM section below).

---

## Agent Registry

### `services` vs `mcpServers` — two views of the same resource
When you create via `gcloud alpha agent-registry services create`, it automatically generates a corresponding `mcpServers/` resource with an auto-generated UUID name (e.g., `agentregistry-00000000-0000-0000-8a2b-759ac8b4962d`). The ADK's `AgentRegistry.get_mcp_toolset()` requires the **full mcpServer resource path**, not the service name:
```python
# WRONG
registry.get_mcp_toolset("mcpServers/finance-mcp-service")

# CORRECT
registry.get_mcp_toolset("projects/PROJECT/locations/REGION/mcpServers/agentregistry-00000000-...")
```
Get the mcpServer name via: `gcloud alpha agent-registry mcp-servers list --location=REGION --format='value(name)'`

### Correct flags for `services create`
The UG and early docs reference `--endpoint-uri` and `--tool-spec-file` — these don't exist. The correct flags are:
```bash
gcloud alpha agent-registry services create SERVICE_NAME \
    --location=REGION \
    --display-name="finance" \
    --interfaces="protocolBinding=jsonrpc,url=https://my-server.run.app/mcp" \
    --mcp-server-spec-type=tool-spec \
    --mcp-server-spec-content='{"tools": [...]}'
```
Valid `protocolBinding` values: `grpc`, `http-json`, `jsonrpc`, `protocol-binding-unspecified`. Not `MCP_SSE`.

**Important:** Keep `--display-name` short (e.g., `"finance"` not `"Finance MCP Server"`). This becomes the `tool_name_prefix` in `get_mcp_toolset()`, producing tool names like `finance_get_account_balance`.

### Agent Registry auth from Reasoning Engine — requires SPIFFE permissions
`AgentRegistry.get_mcp_toolset()` authenticates using the agent's SPIFFE identity (not the RE service account). Granting `roles/agentregistry.viewer` to the RE service account alone does not work — the SPIFFE principal needs the permission directly.

**Status: working** with `roles/owner` on the SPIFFE principal (overly broad; needs refinement to `roles/agentregistry.viewer`).

A `_LazyToolset` wrapper is also required to defer the `get_mcp_toolset()` call past import time, since Agent Runtime runs the module during deploy health checks when the registry isn't reachable yet.

### URL uniqueness constraint
Agent Registry enforces URL uniqueness across services. If a URL is already registered by another service, `services create` will fail with `Interface URL is already in use by another service`. Delete the old service first.

---

## IAM & Permissions

### Reasoning Engine service account needs explicit grants
The RE service account (`service-PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com`) needs these roles for a full Agent Platform setup:

| Role | Why |
|------|-----|
| `roles/cloudtrace.agent` | Cloud Trace telemetry (without this: 401 errors in logs) |
| `roles/storage.objectAdmin` | Write to GCS staging/logs bucket |
| `roles/agentregistry.viewer` | Read Agent Registry (without this: 401 at startup, agent fails to deploy) |
| Custom role with `networkservices.agentGateways.get`, `.use`, `networkservices.operations.get` | Use Agent Gateway |

### MCP tool governance uses IAP Allow Policies (not Deny Policies)
The internal UG originally described IAM Deny Policies for tool governance — this is **outdated**. The actual model uses **IAP Allow Policies** via Agent Gateway:

- Agent Gateway is **default-deny** — unregistered MCP servers are blocked
- You explicitly **allow** specific tools/agents via `roles/iap.egressor` with conditions
- Everything not explicitly allowed is denied

```bash
gcloud beta iap web set-iam-policy policy.json \
    --project=PROJECT_ID \
    --mcpServer=MCP_SERVER_ID \
    --region=REGION
```

Uses `roles/iap.egressor` with CEL conditions like:
```
api.getAttribute('iap.googleapis.com/mcp.tool.isReadOnly', false) == true
```

**Gotcha:** Using IAM Deny Policies with `principalSet://goog/public:all` may trigger org-level IAM policy violations (e.g., in Argolis environments).

---

## Service Extensions

### `timeout` is required
`gcloud service-extensions authz-extensions import` requires a `timeout` field in the YAML, even for managed services like IAP:
```yaml
name: iap-extension
service: iap.googleapis.com
timeout: 10s
```
Without it, schema validation fails.

---

## Agent Runtime / Reasoning Engine

### No `gcloud` CLI for Reasoning Engine
There is no `gcloud ai reasoning-engines` command. Use:
- `agents-cli deploy` for deployment
- `agents-cli deploy --status` to poll
- Python SDK (`vertexai.agent_engines`) for programmatic access
- REST API for operations like force-delete:
  ```bash
  curl -X DELETE "https://REGION-aiplatform.googleapis.com/v1beta1/projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID?force=true" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)"
  ```

### `force=true` needed for deletion with sessions
Reasoning Engines with active sessions cannot be deleted without `force=true`. The Python SDK `engine.delete()` doesn't support `force=True` in all versions — use the REST API instead.

### Deploys can't be cancelled
Once a Reasoning Engine deploy operation starts, it runs to completion or failure. There's no cancel mechanism. Typical deploy time: 5-10 minutes.

### `GOOGLE_CLOUD_LOCATION` vs `GOOGLE_CLOUD_REGION`
These serve different purposes and must not be conflated:
- `GOOGLE_CLOUD_LOCATION=global` — used by Gemini preview models (e.g., `gemini-3-flash-preview`). Setting this to a region breaks model access.
- `GOOGLE_CLOUD_REGION=us-central1` — auto-injected by `agents-cli`, used for regional services like Agent Registry.

### `agents-cli deploy --agent-identity` enables SPIFFE
This creates the agent with `identity_type: AGENT_IDENTITY`, provisions a SPIFFE ID, and grants basic IAM roles. The SPIFFE ID format:
```
principal://agents.global.org-ORGID.system.id.goog/resources/aiplatform/projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID
```

### `agents-cli deploy` uses `vertexai.Client` with v1beta1
Internally, `agents-cli` uses:
```python
client = vertexai.Client(project=..., location=..., http_options={"api_version": "v1beta1"})
```
The v1beta1 API is required for agent identity, gateway config, and other preview features.

---

## MCP Server on Cloud Run

### Use low-level `mcp.server.Server`, not `FastMCP`
`FastMCP` doesn't bind well to Cloud Run's `$PORT`. Use `mcp.server.Server` with Streamable HTTP + `starlette` + `uvicorn`:
```python
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette

session_manager = StreamableHTTPSessionManager(app=server, stateless=True)

async def routing_app(scope, receive, send):
    if scope["path"] == "/mcp":
        await session_manager.handle_request(scope, receive, send)

app = Starlette(lifespan=lifespan)
app.mount("/", app=routing_app)
```
Use `stateless=True` to avoid session affinity issues on Cloud Run. Use raw ASGI routing (not `Starlette.Mount("/mcp", ...)`) to avoid 307 redirects.

Dependencies: `mcp`, `starlette`, `uvicorn` (not `fastmcp`).

### Tool annotations drive governance
The `readOnlyHint` and `destructiveHint` annotations in `toolspec.json` are what the Agent Gateway evaluates:
```json
{
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true
  }
}
```
These must match between the MCP server's `list_tools()` response and the Agent Registry `toolspec.json`.

---

## Observability

### Cloud Trace works out of the box on Agent Runtime
No setup needed — traces are exported automatically. To also get prompt-response logging, set:
```
LOGS_BUCKET_NAME=bucket-name  (no gs:// prefix)
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT
GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true
```

### Missing `opentelemetry-instrumentation-google-genai` causes warnings but not failures
The log warning `telemetry enabled but proceeding without Google GenAI instrumentation` is noisy but non-fatal. Add `opentelemetry-instrumentation-google-genai>=0.1.0` to requirements to suppress it.

---

## `agents-cli` Tips

### Install
```bash
uv tool install google-agents-cli
```

### Key commands
```bash
agents-cli info                    # Show project config
agents-cli deploy                  # Deploy (interactive)
agents-cli deploy --no-wait        # Deploy async
agents-cli deploy --status         # Poll async deploy
agents-cli deploy --list           # List deployed agents
agents-cli run "prompt"            # Test locally
agents-cli run --url URL --mode adk "prompt"  # Test deployed
```

### `--no-confirm-project` for non-interactive use
When automating, always pass `--no-confirm-project` to skip the interactive project confirmation prompt.

### Auto-injected env vars
`agents-cli deploy` automatically sets these env vars (don't duplicate them):
- `GOOGLE_CLOUD_REGION` — the deploy region
- `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true`
- `AGENT_VERSION` — from pyproject.toml
- `NUM_WORKERS=1`
