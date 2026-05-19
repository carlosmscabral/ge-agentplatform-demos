# Experimental

This folder holds demos, templates, and references that **do not yet conform** to the [11 production rules](../CLAUDE.md) defined for this repository.

## Why items are here

| Item | Reason |
|------|--------|
| `_template/` | Uses raw `deploy_agent.py` with `vertexai.Client` instead of `agents-cli scaffold` |
| `governance-demo/` | Requires `agentGatewayConfig` and `context_spec` not yet supported by `agents-cli deploy` |
| `test-agent-gateway-codelab/` | External codelab reference, not a standalone demo |
| `oauth-3lo-keycloak-demo/` | End-to-end works for ~5min after fresh deploy, then fails deterministically post-idle with `ValueError: Context has already been used to create a Connection` (pyOpenSSL race in `agent → iamconnectorcredentials → urllib3/PyOpenSSLContext`). Confirmed unsalvageable code-level: `google-auth`'s `_MutualTlsAdapter` reaches into `ctx_poolmanager._ctx` (pyOpenSSL-only attribute), so neutralizing pyOpenSSL breaks the mTLS path that `iamconnectorcredentials` requires. Needs upstream fix in `google-auth` or `urllib3.contrib.pyopenssl`. Kept here as architectural reference (Agent Identity 3LO + Registry Binding + SPIFFE + FastMCP+JWT). 4 failed fix attempts documented in [its LESSONS.md](oauth-3lo-keycloak-demo/LESSONS.md). |

## Promotion path

Items can be promoted to the repo root once they are adapted to conform to all 8 rules. Typical gaps are:

1. Switching from `deploy_agent.py` to `agents-cli deploy` (or documenting the specific gap)
2. Adding full parameterization via `.env.template`
3. Ensuring `deploy.sh` / `undeploy.sh` idempotency
4. Writing `README.md` and `ARCHITECTURE.md`

## Status

Items in this folder are **not indexed** in the root `README.md` demo table. Each item should maintain its own `README.md` for local context.
