# Governance Demo — Known Gaps

What's working vs. what still needs fixing for a clean, production-ready setup.
Last updated: 2026-05-04.

---

## Status: Working

- MCP server on **Streamable HTTP** (Cloud Run, stateless mode)
- **Agent Registry** discovery via `get_mcp_toolset()` — standard SDK pattern
- Agent deployed to **Agent Runtime** with SPIFFE identity
- End-to-end tool calls: agent → registry → MCP server → response

---

## Gap 1: Agent Gateway — entire flow is unvalidated end-to-end

**Impact:** The core governance story (agent traffic routed through gateway, policies evaluated, write tools blocked) has **never been validated** in this demo. The gateway resource exists, but it's not wired into the agent's runtime path.

**What works today:**
- The Agent Gateway resource itself can be created (`gcloud alpha network-services agent-gateways import`)
- `deploy_agent.py` has the code to attach the gateway at agent creation time
- The governance model is confirmed: **IAP Allow Policies** with `roles/iap.egressor` (default-deny)

**What has NOT been validated:**
1. **Gateway attachment to Agent Engine** — requires project-level allowlist from Agent Platform team. Without it: `400 FAILED_PRECONDITION: Agent Gateway is not enabled for this project`. The project's allowlist status has not been confirmed.
2. **Traffic actually routing through the gateway** — even with attachment, we haven't confirmed the agent's MCP calls go through the gateway vs. direct to Cloud Run.
3. **IAP policy evaluation** — we don't know if the `iap.googleapis.com/mcp.tool.isReadOnly` attribute is correctly populated from the registry's tool annotations (`readOnlyHint`). The attribute mapping is undocumented.
4. **Write tool blocking** — the end goal (`transfer_funds` blocked, `get_account_balance` allowed) has never been tested.

**Root cause of non-validation:** Two deployment blockers:
- `agents-cli deploy` silently drops `agentGatewayConfig` during its update step. `deploy.sh` step 8 uses `agents-cli deploy`, not `deploy_agent.py`.
- The project may not be allowlisted for the Agent Gateway + Agent Engine integration.

**To validate the full flow:**
1. Confirm allowlist status with the Agent Platform team
2. Delete the current agent (`agents-cli deploy --delete` or REST API with `force=true`)
3. Switch `deploy.sh` step 8 to use `deploy_agent.py` (which does a single `create()` with gateway config)
4. Verify traffic routes through the gateway (check gateway logs / Cloud Trace)
5. Apply an IAP Allow Policy granting `roles/iap.egressor` to the agent SPIFFE identity (see `iap-allow-policy.json.example`)
6. Test: `get_account_balance` succeeds, `transfer_funds` is blocked

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

## Gap 4: IAP Allow Policy not applied (requires gateway)

**Impact:** Tool governance is completely inactive. The agent can call any MCP tool without restriction.

**Root cause:** The Agent Gateway is default-deny — all tool access is blocked unless explicitly allowed via IAP Allow Policies. But the gateway isn't attached (Gap 1), so the enforcement point doesn't exist. Without the gateway in the request path, IAP policies are never evaluated.

**Governance model (confirmed):**
- Agent Gateway is **default-deny** (not default-allow)
- You grant `roles/iap.egressor` to the agent's SPIFFE identity with CEL conditions
- The attribute is `iap.googleapis.com/mcp.tool.isReadOnly` (mapped from `readOnlyHint` in the tool spec)
- The old IAM Deny Policy approach (`mcp.googleapis.com/tool.isReadOnly`) is **outdated** and has been removed

**To fix:** Resolve Gap 1 first (attach gateway). Then:
1. Apply an IAP Allow Policy granting the agent's SPIFFE identity `roles/iap.egressor` with a condition on `iap.googleapis.com/mcp.tool.isReadOnly == true`
2. See `iap-allow-policy.json.example` for the template
3. Apply via `gcloud beta iap web set-iam-policy`

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

## Cleanup backlog

Minor items that don't affect functionality:

- [ ] `deployment_metadata.json` schema differs between `agents-cli` and `deploy_agent.py` — standardize
