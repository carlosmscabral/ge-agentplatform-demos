# Governance Demo — Known Gaps

What's working vs. what still needs fixing for a clean, production-ready setup.
Last updated: 2026-04-29.

---

## Status: Working

- MCP server on **Streamable HTTP** (Cloud Run, stateless mode)
- **Agent Registry** discovery via `get_mcp_toolset()` — standard SDK pattern
- Agent deployed to **Agent Runtime** with SPIFFE identity
- End-to-end tool calls: agent → registry → MCP server → response

---

## Gap 1: Agent Gateway not attached to Agent Engine

**Impact:** Tool governance (read-only enforcement via IAP policies) is **not active**. The agent can call any MCP tool without restriction.

**Root cause:** Two blockers:
1. `agents-cli deploy` silently drops `agentGatewayConfig` during its update step (see LEARNINGS.md). A separate `deploy_agent.py` exists that does a single `create()` call, but `deploy.sh` doesn't use it yet.
2. The project may need to be on the **Agent Gateway allowlist** for Agent Engine integration. Without it, attaching a gateway returns `400 FAILED_PRECONDITION`.

**To fix:**
- Confirm allowlist status with the Agent Platform team
- Switch `deploy.sh` step 8 from `agents-cli deploy` to `python deploy_agent.py` (or update `agents-cli` when it gains gateway support)
- Gateway can only be set at creation time — the current agent must be deleted and recreated

---

## Gap 2: `_LazyToolset` wrapper required for Agent Runtime deploys

**Impact:** Code complexity. The agent can't call `get_mcp_toolset()` at module level because Agent Runtime runs the module during deploy health checks, before the MCP server / registry are reachable.

**Root cause:** Agent Runtime imports the agent module and instantiates it to verify the schema. Network calls during import fail (connection refused or auth not ready).

**Fragility:** `_LazyToolset` must match `BaseToolset`'s method signatures exactly. The `get_tools(self, readonly_context=None)` parameter was added by the SDK and broke our wrapper silently (tools just didn't load, no error — model hallucinated tool names).

**To fix:** Either:
- ADK adds a built-in lazy/deferred toolset pattern
- Agent Runtime skips full initialization during health checks
- Or we accept this wrapper as a cost of the registry path and add tests for signature compatibility

---

## Gap 3: SPIFFE identity has `roles/owner` (overly permissive)

**Impact:** Security. The agent's SPIFFE identity has project-level Owner, which is far broader than needed.

**Root cause:** The SPIFFE identity needed `roles/agentregistry.viewer` to call `get_mcp_toolset()` from inside Agent Runtime. During debugging, we granted `roles/owner` as a blunt test and haven't refined it yet.

**To fix:**
1. Remove `roles/owner` from the SPIFFE principal
2. Grant only `roles/agentregistry.viewer` (and possibly `roles/run.invoker` if the MCP server requires auth)
3. Test that the deployed agent still loads tools successfully
4. Add the SPIFFE IAM grants to `deploy.sh`

The SPIFFE principal format:
```
principal://agents.global.org-ORGID.system.id.goog/resources/aiplatform/projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID
```

---

## Gap 4: No tool governance enforcement without gateway

**Impact:** `deploy.sh` step 10 applies an IAM Deny Policy, but without the Agent Gateway attached (Gap 1), there's no enforcement point. The agent bypasses the gateway entirely and calls the MCP server directly via its URL.

**Root cause:** Governance requires the full chain: Agent → Agent Gateway → IAP policy evaluation → MCP server. Without the gateway in the middle, policies aren't evaluated.

**To fix:** Resolve Gap 1 first. Then:
- Confirm whether IAM Deny Policies or IAP Allow Policies are the correct mechanism (LEARNINGS.md notes that IAP Allow Policies are the actual model for Google-managed gateways)
- Update `deploy.sh` step 10 accordingly

---

## Gap 5: MCP server has no authentication

**Impact:** The Cloud Run MCP server is deployed with `--allow-unauthenticated`. Anyone with the URL can call it.

**Root cause:** Simplicity for the demo. Adding auth would require the agent to present credentials, which in turn requires the Agent Gateway or IAM integration to inject them.

**To fix:**
- Remove `--allow-unauthenticated` from `deploy.sh` step 1
- Grant `roles/run.invoker` to the agent's SPIFFE identity (or the RE service account)
- The ADK's `StreamableHTTPConnectionParams` supports auth headers; the registry path may handle this automatically via the gateway

---

## Gap 6: `deploy.sh` step 9 (SPIFFE extraction) doesn't work

**Impact:** Minor. The deploy script tries to read `spiffe_id` from `deployment_metadata.json`, but `agents-cli` doesn't write that field. The step always prints `(not available)`.

**To fix:** Use the Agent Engine API to query the SPIFFE identity after deployment:
```bash
curl -s "https://REGION-aiplatform.googleapis.com/v1beta1/projects/PROJECT/locations/REGION/reasoningEngines/ENGINE_ID" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" | jq -r '.agentIdentity'
```

---

## Gap 7: LEARNINGS.md has stale SSE references

**Impact:** Documentation drift. Several sections still reference SSE transport (`/sse`, `SseServerTransport`, `SseConnectionParams`) which has been replaced by Streamable HTTP.

**Sections to update:**
- "Agent Registry > Correct flags for `services create`" — URL should reference `/mcp` not `/sse`
- "MCP Server on Cloud Run > Use low-level `mcp.server.Server`" — should show `StreamableHTTPSessionManager` pattern, not `SseServerTransport`

---

## Cleanup backlog

Minor items that don't affect functionality:

- [ ] `deploy.sh` step 6 is a no-op placeholder — remove or implement when authz policies work with managed gateways
- [ ] `deployment_metadata.json` schema differs between `agents-cli` and `deploy_agent.py` — standardize
- [ ] `session.db` is tracked in git — should be in `.gitignore`
