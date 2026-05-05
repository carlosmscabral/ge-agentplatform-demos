# Governance Demo — Known Gaps & Findings

What's working, what's not, and what's unclear.
Last updated: 2026-05-05.

---

## Validated (Working End-to-End)

- MCP server on **Streamable HTTP** (Cloud Run, stateless mode)
- **Agent Registry** discovery via `get_mcp_toolset()` — standard SDK pattern
- Agent deployed to **Agent Runtime** with **SPIFFE identity**
- **Agent Gateway** (AGENT_TO_ANYWHERE) attached at Agent Engine creation time
- **IAP authz extension** (DRY_RUN) + **authz profile** on gateway — unblocks traffic
- **Full agent chain through gateway**: Session creation, Gemini LLM calls, MCP tool calls (both `get_account_balance` and `transfer_funds`) all work
- **IAP DRY_RUN audit logs** — every request through the gateway is logged with agent identity, target URL, and grant/deny decision
- **Console-created IAP policies** scoped to MCP server resources (via Agent Platform > Govern > Policies)

---

## Key Architecture Findings

### Deploy Order is Critical

The gateway is **default-deny**. Without an authz extension + policy, the agent can't reach ANY APIs (Gemini, Sessions, MCP servers). The authz extension and policy MUST be created BEFORE deploying the agent:

```
1. MCP server (Cloud Run)
2. Agent Registry
3. Gateway
4. IAP authz extension + authz policy  ← BEFORE agent
5. Deploy agent with gateway attachment
6. Grant SPIFFE identity registry access
```

### Gateway Intercepts ALL Outbound Traffic

When an Agent Engine is deployed with `agentGatewayConfig`, the gateway's mTLS proxy intercepts ALL outbound gRPC/HTTP — not just MCP traffic:

| Traffic | URL Pattern | Gateway Behavior |
|---------|-------------|-----------------|
| MCP tool calls | `finance-mcp-server-*.run.app/mcp` | Intercepted, logged |
| Gemini LLM | `aiplatform.mtls.googleapis.com/.../generateContent` | Intercepted, logged |
| Session service | `us-central1-aiplatform.mtls.googleapis.com/.../sessions/...` | Intercepted, logged |
| Telemetry | `telemetry.googleapis.com/v1/traces` | Intercepted, logged |
| Resource Manager | `cloudresourcemanager.googleapis.com` | **Blocked** (gRPC timeout) |

All URLs are rewritten to `*.mtls.googleapis.com` by the gateway proxy.

### AdkApp.set_up() Crashes Behind Gateway

The `AdkApp.project_id()` method calls `resource_manager_utils.get_project_id()` via gRPC, which is blocked by the gateway. The SDK only catches `PermissionDenied` and `Unauthenticated`, not `RetryError` (timeout).

**Workaround**: `agent_runtime_app.py` monkey-patches `resource_manager_utils.get_project_id` to catch all exceptions and fall back to the raw project value.

### SPIFFE Identity Per Agent Instance

Every new Agent Engine instance gets a unique SPIFFE ID (includes the `reasoningEngines/ENGINE_ID`). The SPIFFE must be granted `roles/agentregistry.viewer` AFTER creation — can't be pre-configured. The `effectiveIdentity` field in the API response may take time to populate; the Agent Registry auto-registration shows it immediately.

### agents-cli Does Not Support Gateway Config

`agents-cli deploy` silently drops `agentGatewayConfig`. Must use `deploy_agent.py` with `vertexai.Client` directly. Also requires `source_packages` + `class_methods` (generated via `_agent_engines_utils`).

---

## Gap 1: MCP Protocol-Level Authz Policies Fail on Google-Managed Gateways

**Impact:** Cannot enforce tool-level blocking (e.g., allow `get_account_balance`, deny `transfer_funds`) using ALLOW/DENY authz policies with MCP `httpRules`.

**What we tried:**
```yaml
# DENY specific tool
action: DENY
httpRules:
  - to:
      operations:
        - mcp:
            methods:
              - name: "tools/call"
                params:
                  - exact: "transfer_funds"

# ALLOW specific tools only
action: ALLOW
httpRules:
  - to:
      operations:
        - mcp:
            baseProtocolMethodsOption: MATCH_BASE_PROTOCOL_METHODS
            methods:
              - name: "tools/call"
                params:
                  - exact: "get_account_balance"
```

**Result:** Both fail with `code: 13, "an internal error has occurred"` after long operation wait. Tested with both short (`projects/...`) and full (`//networkservices.googleapis.com/projects/...`) resource paths.

**Root cause:** MCP protocol parsing for authz policies is documented in the UG but not operational on Google-managed gateways in this release.

---

## Gap 2: IAP Enforcement Blocks Internal Google APIs

**Impact:** Switching IAP from DRY_RUN to enforcement mode (`failOpen: false`, no `iamEnforcementMode: DRY_RUN`) blocks ALL traffic, not just MCP tool calls.

**What happens:**
1. IAP evaluates all gateway traffic against `unregisteredEndpoint` resource
2. Internal APIs (sessions, Gemini) have no IAP policy and get denied
3. `failOpen: true` bypasses ALL denials (can't be per-resource)
4. `failOpen: false` blocks ALL denials (including internal APIs)

**The `unregisteredEndpoint` problem:**
- ALL traffic through the gateway hits IAP as `projects/PROJECT/locations/global/iap_web/agentRegistry/endpoints/unregisteredEndpoint`
- The gateway does NOT resolve MCP server URLs to their registered Agent Registry entries
- Console-created IAP policies (scoped to `iap_web/agentRegistry/mcpServers/MCP_ID`) are never evaluated because traffic never matches that resource path
- The `unregisteredEndpoint` resource cannot have IAP policies set on it via API (404 Not Found)

**Workaround:** Stay in DRY_RUN mode. IAP logs all requests with agent identity and target URL for audit.

---

## Gap 3: IAP Logs Missing MCP Tool-Level Metadata

**Impact:** DRY_RUN logs don't include tool names, `readOnlyHint`, or other MCP protocol attributes. Only the HTTP URL is logged.

**What IAP logs contain:**
- Agent SPIFFE identity (`principalSubject`)
- Target URL (e.g., `https://finance-mcp-server-*.run.app/mcp`)
- DRY_RUN flag
- Grant/deny decision
- IAP resource path (`unregisteredEndpoint`)

**What IAP logs do NOT contain:**
- MCP tool name (`get_account_balance`, `transfer_funds`)
- MCP tool annotations (`readOnlyHint`, `destructiveHint`)
- MCP method (`tools/call`, `tools/list`)
- Tool parameters

The docs reference attributes `iap.googleapis.com/mcp.toolName` and `iap.googleapis.com/mcp.tool.isReadOnly`, but these don't appear in the audit logs.

---

## Gap 4: `_LazyToolset` Wrapper Required

**Impact:** Code complexity. The agent can't call `get_mcp_toolset()` at module level because Agent Runtime imports the module during deploy health checks.

**Fragility:** `_LazyToolset` must match `BaseToolset`'s method signatures. A silent signature mismatch causes tools to not load — the model hallucinates tool names.

---

## Gap 5: MCP Server Has No Authentication

**Impact:** Cloud Run MCP server deployed with `--allow-unauthenticated`. The gateway routes traffic to it, but anyone with the URL can also call it directly.

---

## Unclear / Needs Investigation

- [ ] **Console vs API policy paths**: Console creates policies at `iap_web/agentRegistry/mcpServers/MCP_ID`, but gateway evaluates at `iap_web/agentRegistry/endpoints/unregisteredEndpoint`. Are these supposed to match?
- [ ] **Agent Registry bindings**: Do bindings (`gcloud alpha agent-registry bindings`) affect how the gateway resolves registered endpoints? Bindings require `auth_provider` which suggests they're for delegated auth, not gateway routing.
- [ ] **`--mcpServer` gcloud flag**: Docs show `gcloud beta iap web set-iam-policy --mcpServer=MCP_ID` but the flag doesn't exist in alpha/beta gcloud. When will it be available?
- [ ] **Tool-level metadata in IAP**: When will `mcp.toolName` and `mcp.tool.isReadOnly` attributes be populated in IAP evaluations?
- [ ] **`failOpen` per-resource**: Can `failOpen` behavior be scoped to specific resource types (allow unregistered, enforce registered)?
- [ ] **ADK governance SDK**: UG mentions `from google.adk.governance import AgentGateway` with `AgentGatewayAuthzExt` — is this available in ADK 1.32.0?

---

## Cleanup Backlog

- [ ] `deployment_metadata.json` schema differs between `agents-cli` and `deploy_agent.py`
- [ ] Empty `tests/unit/` directory
- [ ] `deploy.sh` step 8 SPIFFE extraction uses `effectiveIdentity` (may not be populated immediately) — should also check Agent Registry
- [ ] Stale IAP policies at project level (`locations/global/iap_web`, `locations/us-central1/iap_web`) from debugging — clean up
