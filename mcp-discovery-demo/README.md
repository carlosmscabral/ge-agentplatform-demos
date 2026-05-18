# mcp-discovery-demo — Financial Analyst Toolkit

A demo that wires together **three FastMCP servers on Cloud Run**, registered in
**Agent Registry** with category tags, and discovered dynamically at runtime by
an **ADK orchestrator with SPIFFE identity** running on Agent Runtime.

The agent uses **two discovery criteria** — substring keyword/intent search and
tag-based category filtering — to introspect what tools are available before
invoking them.

> **Use case (mocked)**: a Brazilian-Portuguese financial analyst that answers
> questions about quotes, portfolio positions, and news sentiment by routing to
> the right MCP server on demand.

## What this demonstrates

| Capability | How |
|---|---|
| Modern Python MCP servers | FastMCP 2.x with Streamable HTTP transport on `$PORT` |
| MCP on Cloud Run | One Cloud Run service per MCP, `--no-allow-unauthenticated`, JSON-RPC at `/mcp` |
| Dynamic discovery via Agent Registry | `AgentRegistry.list_mcp_servers(filter_str=...)` + client-side filtering |
| SPIFFE workload identity for agents | `agents-cli deploy --agent-identity` + principal-set IAM grants |
| Service-to-service auth | ID-token `header_provider` minted per Cloud Run audience |
| Lazy toolset materialization | `_LazyToolset` wrapper defers `McpToolset` past health-check time |

## Quick start

```bash
cd mcp-discovery-demo
cp .env.template .env             # edit values if you don't want auto-detection
./deploy.sh                       # ~10-15 minutes end-to-end

# Try a query
cd orchestrator-agent
agents-cli run --url "<ORCH_URL from deploy output>" --mode adk \
    "Analise minha posição em PETR4: cotação, PnL e sentimento"
```

When you're done:

```bash
./undeploy.sh
```

## Prerequisites

### Tooling
* GCP project with billing enabled
* `gcloud` CLI authenticated (`gcloud auth login` + `gcloud auth application-default login`)
* `uv` installed (Python 3.11+ resolved automatically)
* `agents-cli` installed: `uv tool install google-agents-cli`

### Required APIs (enable once per project)
```bash
gcloud services enable \
    aiplatform.googleapis.com \
    run.googleapis.com \
    agentregistry.googleapis.com \
    cloudbuild.googleapis.com \
    iamcredentials.googleapis.com \
    logging.googleapis.com \
    monitoring.googleapis.com
```

### IAM (granted automatically by `deploy.sh` Step 2 to the project's SPIFFE principal set)
You (the deployer) need `roles/owner` or equivalent to grant these to the SPIFFE principal set. Run-time agents inherit them:

| Role | Why it's needed |
|---|---|
| `roles/aiplatform.agentDefaultAccess` | Baseline agent capabilities (auto-granted with `--agent-identity` too) |
| `roles/aiplatform.user` | Inference, sessions, Reasoning Engine ops |
| `roles/serviceusage.serviceUsageConsumer` | Use project quota |
| `roles/logging.logWriter` | Write logs from the agent |
| `roles/monitoring.metricWriter` | Emit metrics |
| `roles/cloudapiregistry.viewer` | Read Cloud API Registry (avoid 401 at startup) |
| `roles/storage.objectAdmin` | Read/write the GCS staging bucket |
| `roles/agentregistry.viewer` | **Required** for `AgentRegistry.list_mcp_servers()` — without this, discovery returns `[]` silently |

Each of the 3 Cloud Run MCP services is also granted `allUsers:roles/run.invoker` (public access) — see [`ARCHITECTURE.md` §2c & §8.3-8.5](./ARCHITECTURE.md) for why this is the demo's choice rather than `--no-allow-unauthenticated`.

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `PROJECT_ID` | auto from gcloud | GCP project ID |
| `PROJECT_NUMBER` | auto | Numeric project number (for SPIFFE principal set) |
| `REGION` | `us-central1` | Region for Cloud Run + Reasoning Engine |
| `REGISTRY_LOCATION` | `us-central1` | Agent Registry region |
| `STAGING_BUCKET` | `${PROJECT_ID}-mcp-discovery-staging` | GCS bucket for logs + agent staging |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Model used by the orchestrator |
| `MARKET_MCP_SERVICE` / `PORTFOLIO_MCP_SERVICE` / `NEWS_MCP_SERVICE` | `fintoolkit-*-mcp` | Cloud Run service names |
| `MARKET_MCP_URL` / `PORTFOLIO_MCP_URL` / `NEWS_MCP_URL` | localhost:808x | **Local-dev fallback**. Cloud deploys use Registry-resolved URLs (the agent calls `registry.get_mcp_toolset(name)` — see [`ARCHITECTURE.md` §3](./ARCHITECTURE.md)) |

## Local testing (no cloud)

```bash
# Unit-test each MCP (FastMCP in-memory Client)
cd market-data-mcp && uv run pytest && cd ..
cd portfolio-mcp && uv run pytest && cd ..
cd news-sentiment-mcp && uv run pytest && cd ..

# Spawn all 3 MCPs on free local ports and probe via Streamable HTTP
uv run --with fastmcp python local_test.py

# Orchestrator discovery logic (mocked AgentRegistry)
cd orchestrator-agent && uv run pytest tests/unit/
```

## What's in the repo

```
mcp-discovery-demo/
├── deploy.sh / undeploy.sh        # 9-step idempotent deploy + reverse cleanup
├── local_test.py                  # Multi-server local probe
├── .env.template                  # All env vars, documented
├── market-data-mcp/               # FastMCP server: quotes, history, indexes
├── portfolio-mcp/                 # FastMCP server: holdings, PnL, allocation
├── news-sentiment-mcp/            # FastMCP server: news, sentiment, search
└── orchestrator-agent/            # ADK agent with SPIFFE + dynamic discovery
    └── app/
        ├── agent.py               # 3 _LazyToolsets + 2 discovery FunctionTools
        ├── discovery.py           # AgentRegistry filter helpers
        └── mcp_auth.py            # Cloud Run ID-token header_provider
```

## Further reading

* [`ARCHITECTURE.md`](./ARCHITECTURE.md) — guia técnico em PT-BR da implementação atual (Option B): diagramas de discovery, IAM, fluxo end-to-end, registro no Registry
* [`LESSONS.md`](./LESSONS.md) — histórico em PT-BR de decisões e bugs encontrados (Strategy A → B → discovery load-bearing, tags em Registry, SPIFFE+CR, etc.)
* [`DEMO.md`](./DEMO.md) — roteiro de demo em PT-BR com prompts copy-paste
* Repo-level [`LEARNINGS.md`](../LEARNINGS.md) — padrões reutilizáveis validados nesta demo (FastMCP no CR, Registry tags, SPIFFE+CR)
* Repo-level [`CLAUDE.md`](../CLAUDE.md) — as 11 regras de produção

## Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `AgentRegistry.list_mcp_servers()` returns `[]` | SPIFFE principal missing `agentregistry.viewer` | Re-run `deploy.sh` Step 2 |
| Agent gets `HTTP 401` from Cloud Run | CR is `--no-allow-unauthenticated`, but SPIFFE access tokens aren't accepted by CR IAM | See [`LESSONS.md` §3](./LESSONS.md) — current demo uses `--allow-unauthenticated` |
| Agent fails with `Compute Engine Metadata server unavailable` | Code is calling `id_token.fetch_id_token` (no GCE metadata server in Agent Runtime) | Don't mint ID tokens in Agent Runtime — see [`LESSONS.md` §2](./LESSONS.md) |
| `agents-cli deploy` fails: lockfile is out of date | `pyproject.toml` changed without re-locking | Run `uv lock` in the agent dir (handled automatically by `deploy.sh` Step 5) |
| `gcloud alpha agent-registry services create` rejects `--attributes` / `--labels` / `--tags` | Those flags do not exist; `attributes` is system-reserved readOnly | Encode tags in `--description` as `[tag:X]` — see [`LESSONS.md` §1](./LESSONS.md) and [`ARCHITECTURE.md` §4](./ARCHITECTURE.md) |
| Discovery returns `count: 0` even for clear keyword matches | `_normalize` reading tools from wrong path in Registry response | Fixed — see [`LESSONS.md` §10](./LESSONS.md) |
| Cloud Run cold start makes first request time out | Agent's first call wakes up CR (~5-10s); `agents-cli run` has 120s default timeout | Re-run; subsequent calls are warm. Or set `--min-instances=1` on the MCPs |
| `deployment_metadata.json` carries a stale Reasoning Engine ID | Scaffold leaves one from the template's prior deploy | `rm orchestrator-agent/deployment_metadata.json` before deploy (handled by Step 5+6) |
