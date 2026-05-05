# Governance Demo — Known Gaps

What's working vs. what still needs fixing for a clean, production-ready setup.
Last updated: 2026-05-05.

---

## Status: Working

- MCP server on **Streamable HTTP** (Cloud Run, stateless mode)
- **Agent Registry** discovery via `get_mcp_toolset()` — standard SDK pattern
- Agent deployed to **Agent Runtime** with SPIFFE identity
- End-to-end tool calls: agent -> registry -> MCP server -> response
- **Agent Gateway** resource creation (`gcloud alpha network-services agent-gateways import`)
- **Gateway attachment** at Agent Engine creation via `deploy_agent.py` (project allowlisted)

---

## Resolved: Agent Gateway — project allowlisted

**Previously Gap 1.** The project (`vibe-cabral`) is now allowlisted for Agent Gateway + Agent Engine integration. `deploy_agent.py` uses `vertexai.Client` to create the agent with `agentGatewayConfig` in a single `create()` call. `agents-cli deploy` still silently drops the gateway config — use `deploy_agent.py` instead.

---

## Resolved: SPIFFE extraction

**Previously Gap 6.** SPIFFE identity is now extracted via REST API after deployment instead of reading from `deployment_metadata.json` (which `agents-cli` never wrote). `deploy_agent.py` also writes the SPIFFE ID to `deployment_metadata.json`.

---

## Gap 1: End-to-end tool governance not yet validated

**Impact:** The governance story (gateway routes traffic, authz policy allows read-only tools, blocks write tools) needs end-to-end validation.

**What has been set up:**
1. Agent Gateway resource created (AGENT_TO_ANYWHERE, default-deny)
2. Agent deployed with gateway attachment (`agentGatewayConfig`)
3. Authorization policy template (`authz-allow-readonly.yaml.template`) with MCP tool-name matching
4. IAP authz extension template (`iap-authz-extension.yaml.template`)

**What needs validation:**
1. Traffic actually routing through the gateway (check Cloud Trace / gateway logs)
2. Authz policy applied and evaluated — `get_account_balance` allowed, `transfer_funds` blocked
3. Agent gracefully handles blocked tool calls (reports security policy to user)

---

## Gap 2: `_LazyToolset` wrapper required for Agent Runtime deploys

**Impact:** Code complexity. The agent can't call `get_mcp_toolset()` at module level because Agent Runtime runs the module during deploy health checks, before the MCP server / registry are reachable.

**Root cause:** Agent Runtime imports the agent module and instantiates it to verify the schema. Network calls during import fail (connection refused or auth not ready).

**Fragility:** `_LazyToolset` must match `BaseToolset`'s method signatures exactly. The `get_tools(self, readonly_context=None)` parameter was added by the SDK and broke our wrapper silently (tools just didn't load, no error — model hallucinated tool names).

---

## Gap 3: SPIFFE identity permissions need refinement

**Impact:** Security. The agent's SPIFFE identity may have overly broad permissions.

**Target state:**
- SPIFFE should have `roles/agentregistry.viewer` (for `get_mcp_toolset()`)
- Possibly `roles/run.invoker` (if MCP server requires auth)
- Should NOT have `roles/owner`

`deploy.sh` step 8 now grants `roles/agentregistry.viewer` to the SPIFFE identity automatically.

---

## Gap 4: MCP server has no authentication

**Impact:** The Cloud Run MCP server is deployed with `--allow-unauthenticated`. Anyone with the URL can call it.

**To fix:**
- Remove `--allow-unauthenticated` from `deploy.sh` step 1
- Grant `roles/run.invoker` to the agent's SPIFFE identity (or the RE service account)
- The gateway path may handle authentication automatically

---

## Cleanup backlog

Minor items that don't affect functionality:

- [ ] `deployment_metadata.json` schema differs between `agents-cli` and `deploy_agent.py` — standardize
- [ ] Empty `tests/unit/` directory — add unit tests or remove
