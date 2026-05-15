# Experimental

This folder holds demos, templates, and references that **do not yet conform** to the [8 production rules](../CLAUDE.md) defined for this repository.

## Why items are here

| Item | Reason |
|------|--------|
| `_template/` | Uses raw `deploy_agent.py` with `vertexai.Client` instead of `agents-cli scaffold` |
| `governance-demo/` | Requires `agentGatewayConfig` and `context_spec` not yet supported by `agents-cli deploy` |
| `test-agent-gateway-codelab/` | External codelab reference, not a standalone demo |

## Promotion path

Items can be promoted to the repo root once they are adapted to conform to all 8 rules. Typical gaps are:

1. Switching from `deploy_agent.py` to `agents-cli deploy` (or documenting the specific gap)
2. Adding full parameterization via `.env.template`
3. Ensuring `deploy.sh` / `undeploy.sh` idempotency
4. Writing `README.md` and `ARCHITECTURE.md`

## Status

Items in this folder are **not indexed** in the root `README.md` demo table. Each item should maintain its own `README.md` for local context.
