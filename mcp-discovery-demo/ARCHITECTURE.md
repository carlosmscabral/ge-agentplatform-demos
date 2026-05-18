# mcp-discovery-demo — Architecture

This document walks through the design of the Financial Analyst Toolkit demo,
the trade-offs made, and the reasoning behind each layer.

---

## 1. System view

```
                         ┌──────────────────────────────────────────────┐
                         │             Agent Runtime (Vertex AI)        │
                         │                                              │
                         │   ┌────────────────────────────────────────┐ │
                         │   │  fintoolkit-orchestrator (ADK + SPIFFE) │ │
                         │   │                                        │ │
   user ──── A2A/ADK ───►│   │  tools:                                │ │
                         │   │   • market   (lazy MCPToolset)         │ │
                         │   │   • portfolio (lazy MCPToolset)        │ │
                         │   │   • news     (lazy MCPToolset)         │ │
                         │   │   • discover_tools_by_intent  (FT)     │ │
                         │   │   • discover_tools_by_category (FT)    │ │
                         │   └─────────────────┬──────────────────────┘ │
                         └─────────────────────┼────────────────────────┘
                                               │
                ┌──────────────────────────────┼────────────────────────────┐
                │                              │                            │
                ▼                              ▼                            ▼
   ┌────────────────────────┐  ┌────────────────────────┐  ┌────────────────────────┐
   │   Agent Registry       │  │   Cloud Run (3 svcs)   │  │   Cloud Trace + Logs    │
   │                        │  │                        │  │                         │
   │  mcpServers/...        │  │  market-data-mcp       │  │  spans + payloads via   │
   │   ├ market-data        │  │  portfolio-mcp         │  │  OTEL_…_EVENT_ONLY      │
   │   ├ portfolio          │  │  news-sentiment-mcp    │  │                         │
   │   └ news-sentiment     │  │  (FastMCP 2.x,         │  └─────────────────────────┘
   │  attributes.tag=...    │  │   Streamable HTTP)     │
   │  toolspec inlined      │  │  --no-allow-…          │
   └────────────────────────┘  └────────────────────────┘
```

---

## 2. Why three things matter

### a) FastMCP 2.x — modern Python MCP, not the low-level `mcp.server`

[LEARNINGS.md](../LEARNINGS.md) line 196 noted that FastMCP **1.x** didn't bind
to Cloud Run's `$PORT` properly and recommended the low-level `mcp.server.Server`
+ Starlette + uvicorn pattern. We validated that **FastMCP 2.x** (this demo
uses `>=2.13`) binds cleanly via:

```python
mcp.run(transport="http", host="0.0.0.0", port=int(os.environ["PORT"]), path="/mcp")
```

Validation evidence: `market-data-mcp/tests/test_http_wire.py` spawns the server
in a subprocess on a free port and probes `list_tools` + `call_tool` via the
FastMCP `Client(url)`. All three MCPs pass the same probe in `local_test.py`.

### b) SPIFFE-bound access tokens for service-to-service auth

The auth story here had a learning curve worth documenting.

**What didn't work**: my first instinct was to mint OIDC ID tokens via
`google.oauth2.id_token.fetch_id_token(request, audience=cr_url)`. This is the
canonical pattern for *Cloud Run → Cloud Run* calls. But it **fails inside
Agent Runtime**:

```
Failed to retrieve http://metadata.google.internal/.../identity?audience=...
Compute Engine Metadata server unavailable. Response status: 500
```

Agent Runtime does not expose the GCE metadata server, so `fetch_id_token` has
no source.

**What does work**: Cloud Run's IAM enforcer accepts the agent's existing
SPIFFE-bound *access token* directly. The SPIFFE certificate is bound to the
token (mTLS to googleapis.com), and IAM extracts the `principal://...` from
that binding. As long as the principal has `roles/run.invoker` on the target
Cloud Run service (`deploy.sh` Step 8), the call is authorized.

Solution in [`orchestrator-agent/app/mcp_auth.py`](./orchestrator-agent/app/mcp_auth.py):

```python
_creds, _ = google.auth.default()
_request = google.auth.transport.requests.Request()

def make_cr_header_provider(audience: str):
    def provider(_ctx=None):
        if not _creds.valid:
            _creds.refresh(_request)
        return {"Authorization": f"Bearer {_creds.token}"}
    return provider
```

The `audience` parameter is preserved for API parity / logging, but doesn't
affect the header — the token binding is what IAM checks.

**This pattern works because:** agentDefaultAccess grants the SPIFFE principal
the right to issue tokens that include the SPIFFE claim, and Cloud Run's IAM
backend was extended to accept the SPIFFE format for `principal://` IAM members.

### c) Why Cloud Run doesn't run with SPIFFE itself (yet)

Today, **only Agent Runtime and Gemini Enterprise** support SPIFFE / Agent
Identity. Cloud Run does not. The closest available identity isolation is a
dedicated service account per service. Managed Workload Identity (Preview)
supports GKE and Compute Engine; if/when Cloud Run is added, this demo can
swap to a fully end-to-end SPIFFE chain by simply adding `--agent-identity`-
equivalent flags to `gcloud run deploy`.

For now, the SPIFFE identity is the *agent-side* security boundary — every
request from the orchestrator carries the SPIFFE principal, and Cloud Run IAM
audits that principal. The Cloud Run service itself runs under the project's
default Compute Engine SA (sufficient for serving the MCP; it doesn't need
elevated permissions because the MCP tools are stateless reads on in-memory
mock data).

### c) Eager toolset construction (demo simplicity)

This demo builds the 3 `McpToolset` instances **eagerly** at module import
time:

```python
market_toolset    = _build_toolset("MARKET_MCP_NAME", "MARKET_MCP_URL", "market")
portfolio_toolset = _build_toolset("PORTFOLIO_MCP_NAME", "PORTFOLIO_MCP_URL", "portfolio")
news_toolset      = _build_toolset("NEWS_MCP_NAME", "NEWS_MCP_URL", "news")
```

Each `_build_toolset` resolves the Registry resource name via
`registry.get_mcp_toolset(name)` — that GETs the MCPServer + bindings (2
HTTP calls to Agent Registry per toolset) and returns the configured
`McpToolset`. The actual MCP server (Cloud Run) is contacted lazily by ADK
on the first `get_tools()`, so the only eager cost at import time is the
Registry calls themselves.

> **Trade-off vs. the `_LazyToolset` wrapper pattern.**
>
> [LEARNINGS.md](../LEARNINGS.md) documents `_LazyToolset` — a `BaseToolset`
> subclass that defers `McpToolset` construction until the first
> `get_tools()` call. That's the right call for production deployments where
> Agent Runtime may import the module during deploy-time health checks
> *before* the Registry or MCP services are reachable. It's used in
> `experimental/governance-demo/`.
>
> For this demo we chose the simpler eager path because (a) the focus is the
> discovery pattern, not deploy resilience, (b) the Registry has been
> provisioned by the time Step 6 of `deploy.sh` runs, and (c) the eager
> failure mode is louder and easier to debug ("deploy crashed at import"
> beats "agent silently returns no tools"). For production, copy the
> `_LazyToolset` from `experimental/governance-demo/demo-agent/app/agent.py`.

---

## 3. Discovery flow

```
user prompt
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│ Gemini decides: "user is asking about sentiment → I should       │
│ discover what's available before invoking blindly"               │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼
discover_tools_by_intent(intent="sentiment")        ◄── FunctionTool
    │
    ▼
discovery._list_all()
    │  AgentRegistry(project, location).list_mcp_servers()
    ▼
[{display_name: "market-data",   attributes: {tag: "market"}, ...},
 {display_name: "portfolio",     attributes: {tag: "portfolio"}, ...},
 {display_name: "news-sentiment",attributes: {tag: "news"}, ...}]
    │
    ▼
filter: substring "sentiment" in display_name OR description
    │
    ▼
returns [news-sentiment] to LLM
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│ Gemini: "news-sentiment has get_sentiment_score → call it"       │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼
news_get_sentiment_score(ticker="AAPL")  ◄── pre-loaded _LazyToolset
    │
    ▼
_LazyToolset._resolve() → McpToolset(StreamableHTTPConnectionParams(
    url="https://news-sentiment-...run.app/mcp",
    header_provider=make_cr_header_provider("https://news-sentiment-...run.app")
))
    │
    ▼  (mints ID token, audience = service base URL)
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  FastMCP 2.x on Cloud Run                                        │
│   • IAM checks: principal://<orch-spiffe> has roles/run.invoker  │
│   • Streamable HTTP /mcp                                         │
│   • Dispatches to @mcp.tool() get_sentiment_score                │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼ JSON-RPC response
    │
    ▼  back through MCPToolset → ADK → Gemini → user
```

### Why two discovery patterns, not one?

The user asked for **two criteria** to demonstrate that discovery isn't a single
hard-coded query. Each criterion exercises a different filter shape:

| Pattern | Filter source | Best when |
|---|---|---|
| `discover_tools_by_intent(intent)` | substring on `displayName`, `description`, **and every tool's name + description** | user phrasing is free-form |
| `discover_tools_by_category(tag)` | parsed `[tag:X]` markers in description (Registry has no writable `attributes`) | agent has narrowed the domain |

The intent search returns a `matched_in` array on each result listing exactly
where the keyword hit (e.g. `["display_name"]`, `["tool:get_stock_quote:name"]`,
`["description", "tool:get_company_news:description"]`), so the LLM can explain
its choice — and so future iterations can rank matches by which level matched.

We deliberately **do not** call this "semantic search" — `AgentRegistry` does
not have an embedding index. The Registry's own `searchMcpServers` REST endpoint
only knows `mcpServerId | name | displayName`; for matching against tool-level
content we built the search client-side over the full `mcpServerSpec.toolSpec`.
True semantic discovery would require Vertex AI Vector Search on top of
registry metadata, which is out of scope for this demo.

### Why pre-load 3 toolsets AND expose discovery?

We considered registering toolsets in runtime after a discovery call, but ADK's
agent loop doesn't gracefully accept new toolsets mid-conversation. The hybrid
chosen here:

* **Pre-load** 3 `_LazyToolset`s — guarantees the LLM can invoke any tool
* **Expose** discovery as introspection — the LLM learns *what's there* and
  *which MCP owns it* and uses that to choose the right call

This keeps the narrative ("the agent discovers its tools") truthful without
fighting the framework. ARCHITECTURE: introspective discovery, not dynamic
tool registration.

### URL resolution: Strategy B (Registry-resolved)

Each `_LazyToolset` reads a Registry resource name from env (e.g.
`MARKET_MCP_NAME=projects/{P}/locations/{L}/mcpServers/agentregistry-…`) and
calls `registry.get_mcp_toolset(name)` on first use. That call:

1. GETs the `MCPServer` resource from Agent Registry
2. Extracts `interfaces[].url` (the Cloud Run URL)
3. Returns a configured `McpToolset`

**Registry is the source of truth for URLs.** If a Cloud Run service URL
changes (e.g., redeploy to a different region), the registry update is enough
— no env-var change, no agent redeploy.

For local development (no Registry entries for `localhost`), each LazyToolset
also reads a fallback env var (`MARKET_MCP_URL=http://localhost:8081/mcp`). If
the `*_NAME` is unset, the `*_URL` is used directly. This is the only place
direct URLs appear.

> **What we tried first** ("Strategy A"): pre-bake the 3 Cloud Run URLs into
> env vars (`MARKET_MCP_URL=https://…run.app/mcp`). The Registry was only used
> as introspection — discovery returned metadata, but toolsets bypassed it.
> See [§8.11 Lessons Learned](#811-strategy-a-pre-baked-urls-was-decorative-registry-usage) for the rationale of switching.

---

## 4. Identity & IAM model

```
deploy.sh Step 2
    │
    ▼
SPIFFE principal SET (all agents in this project)
    principalSet://agents.global.{org|project}-…/attribute.platformContainer/…
    │
    ▼ baseline roles
      ✓ roles/aiplatform.agentDefaultAccess
      ✓ roles/aiplatform.user
      ✓ roles/serviceusage.serviceUsageConsumer
      ✓ roles/logging.logWriter
      ✓ roles/monitoring.metricWriter
      ✓ roles/cloudapiregistry.viewer
      ✓ roles/storage.objectAdmin
      ✓ roles/agentregistry.viewer       ◄── LEARNINGS.md L96: SPIFFE, not RE SA
    │
    ▼
deploy.sh Step 6: agents-cli deploy --agent-identity
    │  Agent Runtime mints SPIFFE cert (24h, auto-rotated)
    ▼
Specific SPIFFE PRINCIPAL (this orchestrator only)
    principal://agents.global.…/resources/aiplatform/…/reasoningEngines/<id>
    │
    ▼
deploy.sh Step 3: Cloud Run public access
    gcloud run services add-iam-policy-binding <svc>
        --member="allUsers" --role="roles/run.invoker"
    │  (see §2c — Cloud Run doesn't support SPIFFE; CR IAM rejects
    │   the agent's SPIFFE access tokens with 401)
    ▼
At runtime: agent calls Cloud Run with bearer = SPIFFE access token.
            Cloud Run ignores the token (public). FastMCP receives the request.
            (The bearer is still useful in CR access logs and for a future
             app-layer auth middleware or Agent Gateway in front.)
```

### IAM bindings reference (everything that gets created)

| Scope | Principal | Role | Purpose |
|---|---|---|---|
| Project | `principalSet://agents.global.{org\|project}-…/attribute.platformContainer/aiplatform/projects/{N}` | `roles/aiplatform.agentDefaultAccess` | Baseline agent capabilities |
| Project | same principalSet | `roles/aiplatform.user` | Inference, sessions |
| Project | same principalSet | `roles/serviceusage.serviceUsageConsumer` | Project quota |
| Project | same principalSet | `roles/logging.logWriter` | Write logs |
| Project | same principalSet | `roles/monitoring.metricWriter` | Emit metrics |
| Project | same principalSet | `roles/cloudapiregistry.viewer` | Read Cloud API Registry |
| Project | same principalSet | `roles/storage.objectAdmin` | Read/write staging bucket |
| Project | same principalSet | `roles/agentregistry.viewer` | **Required for `AgentRegistry.list_mcp_servers()`** |
| Each Cloud Run service | `allUsers` | `roles/run.invoker` | Public access (see §2c for why this is the demo's choice) |

The orchestrator's *specific* SPIFFE principal (`principal://…/reasoningEngines/<id>`) inherits all the principalSet roles. Per-agent grants aren't needed for this demo.

### What breaks if you skip a step?

| Skip | Symptom |
|---|---|
| `agentregistry.viewer` on SPIFFE principal set | `AgentRegistry.list_mcp_servers()` returns 401 → both discovery tools return `[]` → agent says "no MCP found" |
| `allUsers:run.invoker` (or any CR auth grant) | Agent sees `HTTP/1.1 401 Unauthorized` from CR, MCPToolset.get_tools() fails, agent reports "tool not found" |
| `--agent-identity` flag on `agents-cli deploy` | Agent uses Reasoning Engine SA, `principalSet://agents.global…` IAM grants don't apply, Registry calls 401 |
| `_LazyToolset` wrapper (eager MCPToolset construction) | Agent fails health check during deploy: registry/Cloud Run not reachable at import time |
| `uv lock` before `agents-cli deploy` after pyproject change | `agents-cli` fails: `uv export --locked` finds stale lockfile (Rule #6) |

---

## 8. Lessons Learned (what we got wrong during development)

This section is a deliberate record of mistakes made and corrected during the
build of this demo. Each one is a non-obvious failure mode worth remembering
on the next deploy.

### 8.1 We assumed Agent Registry had `--attributes` for tags

**The mistake**: First version of `deploy.sh` called
`gcloud alpha agent-registry services create … --attributes="tag=market,domain=finance"`.
The CLI rejected with `unrecognized arguments: --attributes=...`.

**Reality** (verified against `https://agentregistry.googleapis.com/$discovery/rest?version=v1alpha`):

* `MCPServer.attributes` is `readOnly` + system-reserved. The only valid keys
  are `agentregistry.googleapis.com/system/RuntimeIdentity` and
  `…/system/RuntimeReference`. The platform populates them — users cannot.
* `Service` (the create envelope) has no `labels`, no `tags`, no
  user-writable `attributes` field. The only free-form writable field is
  `description` (2048 chars).
* Custom fields injected into `mcpServerSpec.content` (e.g., `_meta`, `tags`
  at top level) are silently stripped by the API.

**Fix**: encode tags inline in `--description` as `[tag:X] [domain:Y] …`
markers and parse them client-side (`app/discovery.py:_parse_attributes`).
The repo's `experimental/governance-demo/` uses the same workaround.

### 8.2 `id_token.fetch_id_token()` does not work in Agent Runtime

**The mistake**: First `mcp_auth.py` minted OIDC ID tokens via
`google.oauth2.id_token.fetch_id_token(request, audience=<cr_url>)`. This is
the canonical pattern for Cloud Run → Cloud Run calls.

**Symptom in cloud logs**:

```
Could not fetch URI /computeMetadata/v1/instance/service-accounts/default/identity?audience=…
Compute Engine Metadata server unavailable. Response status: 500
```

**Reality**: Agent Runtime does not expose the GCE metadata server. The
`fetch_id_token` family in `google.auth` is hardcoded to that source.

**Fix**: switched to sending the SPIFFE-bound access token as `Bearer …`.
This doesn't help with CR IAM (see 8.3), but it does land in CR access logs
for forensic visibility.

### 8.3 SPIFFE-bound access tokens are NOT accepted by Cloud Run IAM

**The mistake**: Assumed that since `roles/run.invoker` was granted to the
agent's SPIFFE `principal://…`, sending the SPIFFE-bound access token would
work as a Bearer for IAM-protected Cloud Run.

**Symptom**: `HTTP/1.1 401 Unauthorized` from Cloud Run on every MCP call.

**Reality** (cross-referenced with `agent-platform-debugger` skill docs):

* Cloud Run IAM accepts OIDC ID tokens, not OAuth access tokens.
* The SPIFFE token is **DPoP-bound** — cryptographically tied to an X.509 cert.
  Cloud Run has no mTLS terminator to validate the binding.
* The IAM binding `roles/run.invoker → principal://<spiffe>` is **only
  meaningful inside the IAP plane** (i.e., when Agent Gateway sits in front
  and IAP forwards the request as its own service agent).

**Fix**: drop `--no-allow-unauthenticated`. Cloud Run is `--allow-unauthenticated`
for this demo. The trade-off is acknowledged in §2c.

### 8.4 Cloud Run does not support SPIFFE / Agent Identity (today)

**The desire**: Have the Cloud Run MCP services also run with SPIFFE so the
entire call chain is SPIFFE-bound.

**Reality** (verified via Google docs at
`docs.cloud.google.com/iam/docs/agent-identity-overview` and
`docs.cloud.google.com/iam/docs/managed-workload-identity`):

* SPIFFE / Agent Identity is supported for **Agent Runtime** (Vertex AI
  Reasoning Engines) and **Gemini Enterprise** only.
* Managed Workload Identities (the broader SPIFFE program) supports **GKE
  Autopilot** and **Compute Engine** in Preview — but **not Cloud Run**.
* No `--agent-identity`, `--workload-identity`, `--identity-type` flag exists
  on `gcloud run deploy`.

**Fix**: documented limitation in §2c. The only SPIFFE end-to-end path today
(without Agent Gateway) is to migrate the MCPs to GKE with Workload Identity.
Out of scope for this demo.

### 8.5 No documented path for Agent Runtime → private Cloud Run without Agent Gateway

**The mistake**: Tried multiple workarounds (ID tokens, access tokens,
impersonation) hoping one would work.

**Reality** (from `references/policies.md` in the `agent-platform-debugger`
skill):

> A Cloud Run-backed MCP server is invoked **by IAP**, not by the agent
> directly. IAP signs the forwarded request as the IAP service agent …

The Agent Platform's certificate-bound DPoP tokens are **architecturally
designed to terminate at Agent Gateway**. Without that gateway, there is no
sanctioned mechanism for an Agent Runtime SPIFFE agent to authenticate to a
private Cloud Run service.

**Practical options without Agent Gateway**:

| Option | Cost | Pros | Cons |
|---|---|---|---|
| A. `--allow-unauthenticated` (this demo) | Zero | Simple, works | Public on the internet |
| B. App-layer auth (FastMCP middleware validates `tokeninfo`) | Modest | Keeps CR public but validates principal | Adds latency; tokeninfo may not work with DPoP tokens |
| C. Migrate MCPs to GKE with Managed Workload Identity | High | Full SPIFFE end-to-end | Big refactor; GKE ops overhead |
| D. Add Agent Gateway | Medium | Documented, production-correct | Requires project allowlist + extra infra |

### 8.6 `_LazyToolset` wrapper is essential

**The mistake**: Initial agent module eagerly constructed `McpToolset(…)` at
import time. Agent Runtime imports the module during deploy health checks
when MCP services aren't necessarily reachable yet.

**Symptom** (intermittent): deploys hang at "Updating agent" then time out, or
agent comes up but `get_tools()` returns an empty set.

**Fix**: copy the `_LazyToolset` pattern from
`experimental/governance-demo/demo-agent/app/agent.py`. It defers the inner
`McpToolset` construction to first `get_tools()` call. Also documented in
`LEARNINGS.md` L100.

### 8.7 Cloud Run from-source deploys rebuild every time (no Skaffold cache)

**Observation**: each `gcloud run deploy --source=.` triggers a Cloud Build
that rebuilds the container from scratch (3-5 minutes per service).

**Mitigation in this demo**: `deploy.sh` Step 3 launches all 3 builds in
parallel via background subshells + `wait`. Sequential would take 12-15 min;
parallel takes ~3-4 min.

### 8.8 `agents-cli deploy` requires `uv lock` after every `pyproject.toml` change

**The mistake**: Forgot to `uv lock` after editing `pyproject.toml` to add
dependencies; `agents-cli deploy` failed with
`uv export --locked: lockfile is out of date`.

**Fix**: `deploy.sh` Step 5 runs `uv lock --quiet` before every
`agents-cli deploy`. This is Rule #6 in the repo's `CLAUDE.md`. The repo
even has a dedicated commit (777b525) for this.

### 8.9 `agents-cli scaffold create` leaves a stale `deployment_metadata.json`

**The mistake**: After `agents-cli scaffold create`, the freshly-scaffolded
project shipped a `deployment_metadata.json` with someone else's resource ID.
This file is in the demo-level `.gitignore` but not the scaffold's own
`.gitignore`, so it would have been committed.

**Fix**: `deploy.sh` cleans it up if present; updated the demo's `.gitignore`
to use `**/deployment_metadata.json` to catch nested instances.

### 8.11 Strategy A (pre-baked URLs) was decorative Registry usage

**The mistake**: First version of `deploy.sh` passed Cloud Run URLs as env
vars (`MARKET_MCP_URL=https://…run.app/mcp` etc.) to the orchestrator. The
agent's `_LazyToolset` then constructed `McpToolset(StreamableHTTPConnectionParams(url=...))`
manually from those env vars.

**The problem**: Agent Registry was decorative — `discover_tools_by_*` returned
server metadata, but the LLM invoked toolsets that had been pre-cabled by
deploy.sh. If a URL changed in the Registry, the agent didn't notice. The
whole "Registry as source of truth for MCP discovery" pitch was unrealized.

**Fix (Strategy B)**: deploy.sh now passes the **Registry resource names**
(e.g., `MARKET_MCP_NAME=projects/…/mcpServers/agentregistry-…`), not URLs. The
`_LazyToolset` calls `registry.get_mcp_toolset(name)` on first use, which GETs
the MCPServer resource and extracts the URL from `interfaces[].url`. Registry
becomes load-bearing. Direct URLs are kept only as a local-dev fallback when
the `*_NAME` env is unset.

Code change scope: ~30 lines across `deploy.sh` (Step 6 env vars), `agent.py`
(LazyToolset signature), and `discovery.py` (new `build_toolset_from_registry`
helper that wraps `registry.get_mcp_toolset`). No MCP server changes; no
behavior change for `discover_tools_by_*`.

### 8.10 toolspec custom fields are silently stripped — `Annotations.title` is the only writable per-tool string

**The mistake**: Tried to put server-level or tool-level tags inside
`mcpServerSpec.content` via `_meta`, top-level `tags`, or extra annotation keys.

**Reality** (verified empirically by inserting custom fields and reading them
back via `gcloud alpha agent-registry mcp-servers describe`):

* `mcpServerSpec.content` is validated against the MCP `tools/list` schema.
  Unknown top-level fields disappear.
* Per-tool: only `name`, `description`, `inputSchema`, and `annotations` keys
  inside the standard set (`title`, `readOnlyHint`, `destructiveHint`,
  `idempotentHint`, `openWorldHint`) survive. `Annotations.title` is the only
  free-form string — but semantically it's a human-readable title.

**Fix**: stick with the `description` `[tag:X]` workaround (see 8.1).

---

---

## 5. Registration shape in Agent Registry

```yaml
# what `gcloud alpha agent-registry mcp-servers describe <name>` returns
name: projects/{P}/locations/{R}/mcpServers/agentregistry-<uuid>
displayName: market-data                # becomes tool_name_prefix
description: '[tag:market] [domain:finance] market-data MCP server for the fintoolkit demo.'
interfaces:
- protocolBinding: jsonrpc              # JSON-RPC over Streamable HTTP
  url: https://fintoolkit-market-data-mcp-…run.app/mcp
mcpServerSpec:
  toolSpec:
    tools:
    - name: get_stock_quote
      description: Get latest quote (...)
      inputSchema: { ... }
      annotations: { readOnlyHint: true, idempotentHint: true }
    - ...
attributes:                              # readOnly — system-populated only
  agentregistry.googleapis.com/system/RuntimeIdentity: ...  # not populated for CR-hosted MCPs
```

### Why tags live inside `description`, not `attributes`

`MCPServer.attributes` is **readOnly and system-reserved** in the
`agentregistry.v1alpha` API (verified against the discovery document at
`https://agentregistry.googleapis.com/$discovery/rest?version=v1alpha`). The
only keys ever written there by the platform are:

* `agentregistry.googleapis.com/system/RuntimeIdentity` — populated only when
  the underlying runtime has Agent Identity (e.g., Agent Runtime). Cloud Run
  doesn't qualify today, so this field stays empty for our 3 MCPs.
* `agentregistry.googleapis.com/system/RuntimeReference` — internal pointer.

There is no `labels` field and no user-writable `attributes` field on either
`Service` or `MCPServer`. The only writable free-form metadata is
`description` (2048 chars).

So `deploy.sh` Step 4 prefixes the description with `[tag:X] [domain:Y]`
markers, and `app/discovery.py:_parse_attributes()` extracts them client-side
when the orchestrator filters by category. This is the documented workaround
in [`LEARNINGS.md`](../LEARNINGS.md), and the same approach the
`experimental/governance-demo/` uses.

`toolspec.json` files (one per MCP) are the source of truth — they mirror the
`@mcp.tool()` decorators in `app/main.py`. Keep them in sync; mismatch causes
"tool not found" at the gateway layer (if you later add Agent Gateway).

---

## 6. Local-first development loop

The flow we follow (per Rule #3):

```
unit tests (FastMCP in-memory Client)
       │
       ▼  green
HTTP wire smoke (subprocess + Client(url))
       │
       ▼  green
local_test.py — all 3 MCPs at once
       │
       ▼  green
agents-cli run "..." with MCP_URL=localhost:…
       │
       ▼  green
./deploy.sh  ← only now does cloud cost a thing
       │
       ▼  green
agents-cli run --url <orch-cloud-url> --mode adk "..."
       │
       ▼  trace verified in Cloud Trace
done
```

---

## 7. Why the integration tests don't talk to the LLM

`tests/integration/test_agent.py` from the scaffold uses the default
weather/time tools that we removed. We intentionally do **not** add an
integration test that runs the full Gemini loop locally:

* It would require a configured Gemini key, billable
* It would be flaky (depends on model output)
* The valuable surface — the discovery logic — is already covered by
  `tests/unit/test_discovery.py` with a mocked registry

The "real" integration test is the Cloud Trace inspection after `./deploy.sh`.
See [`DEMO.md`](./DEMO.md) for verification steps.
