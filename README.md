# Google Agent Platform Demos

Production-ready demos for Google Cloud's Agent Platform, built with [ADK](https://github.com/google/adk-python) and [`agents-cli`](https://pypi.org/project/google-agents-cli/).

Each demo is fully parameterized — clone, set your GCP project, and deploy.

## Demo Index

| Demo | Description | Key Features |
|------|-------------|--------------|
| [`sessions-memory-demo/`](sessions-memory-demo/) | Cross-session persistence with Memory Bank | ADK Sessions, Memory Bank topics, custom tools, `deploy_agent.py` (agents-cli gap: `context_spec`) |
| [`evals-demo/`](evals-demo/) | Online evaluation and monitoring | `agents-cli deploy`, Gen AI Evaluation Service, online monitors, traffic generation |
| [`a2a-demo/`](a2a-demo/) | Agent-to-Agent protocol on Agent Runtime | A2A protocol, RemoteA2aAgent, agent cards, multi-agent sequential deploy |
| [`spiffe-registry-demo/`](spiffe-registry-demo/) | SPIFFE identity + Agent Registry discovery | SPIFFE `--agent-identity`, auto-registration, `AgentRegistry.get_remote_a2a_agent()`, dynamic discovery |
| [`mcp-discovery-demo/`](mcp-discovery-demo/) | Financial Analyst Toolkit — fully dynamic MCP discovery + invocation | 3 FastMCP servers on Cloud Run (JSON-RPC), Agent Registry as source of truth, SPIFFE orchestrator with ONLY 3 tools (discover by intent/category + `invoke_mcp_tool` router), zero pre-loaded toolsets — new MCPs become reachable without redeploy |
| [`code-execution-demo/`](code-execution-demo/) | Data Analyst with Agent Engine sandbox code execution | `AgentEngineSandboxCodeExecutor` + SPIFFE identity, pre-created sandbox-host Reasoning Engine, stateful sandbox per session (variables persist across turns), ~40 data science libs (pandas/numpy/matplotlib/sklearn/...), no network/no-pip-install/resource-limited, full audit trail in Cloud Trace |
| [`oauth-3lo-keycloak-demo/`](oauth-3lo-keycloak-demo/) | Agent Identity 3-Legged OAuth with Keycloak-protected MCP | SPIFFE agent + Keycloak (any OIDC IdP works), `gcloud alpha agent-identity connectors`, Agent Registry Binding (agent→MCP→auth_provider), FastMCP with PyJWT/JWKS middleware, frontend with `adk_request_credential` consent popup + `/validateUserId` finalize, Google-managed token vault — agent code never sees the raw user token |

> Experimental demos and references are in [`experimental/`](experimental/) — see its README for details.

## Prerequisites

- **gcloud CLI** — authenticated (`gcloud auth login && gcloud auth application-default login`)
- **uv** — Python package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **agents-cli** — ADK CLI tool (`uv tool install google-agents-cli`)
- **GCP project** — with billing enabled and Vertex AI API activated

## Quick Start

```bash
# Pick a demo
cd <demo-name>/

# Configure
cp .env.template .env
# Edit .env — at minimum, verify PROJECT_ID

# Deploy
./deploy.sh

# Test
agents-cli run --url <agent-url> --mode adk "your test prompt"

# Cleanup
./undeploy.sh
```

## Repository Structure

```
ge-agentplatform-demos/
├── CLAUDE.md                     Development standards (8 production rules)
├── LEARNINGS.md                  Hard-won implementation knowledge
├── README.md                     This file
│
├── sessions-memory-demo/         Sessions + Memory Bank demo
│   ├── .env.template
│   ├── deploy.sh / undeploy.sh
│   ├── README.md / ARCHITECTURE.md
│   └── demo-agent/
│
├── a2a-demo/                     A2A protocol demo (multi-agent)
│
├── spiffe-registry-demo/         SPIFFE identity + Agent Registry discovery
│
├── mcp-discovery-demo/           Dynamic MCP discovery (3 FastMCP servers on Cloud Run + SPIFFE orchestrator)
│
├── code-execution-demo/          Data Analyst with Agent Engine sandbox code execution (SPIFFE + stateful sandbox)
│
├── oauth-3lo-keycloak-demo/      Agent Identity 3LO with Keycloak-protected MCP (SPIFFE + Registry Binding + JWT)
│
├── evals-demo/                   Online evaluation demo
│   ├── .env.template
│   ├── deploy.sh / undeploy.sh
│   ├── README.md / ARCHITECTURE.md
│   └── demo-agent/
│
└── experimental/                 Non-conforming demos & references
    ├── setup-project.sh          IAM setup for SPIFFE identity demos
    ├── _template/
    ├── governance-demo/
    └── test-agent-gateway-codelab/
```

## Development Standards

All demos at root conform to the [11 production rules](CLAUDE.md):

1. **Full Parameterization** — env vars for everything, no hardcoding
2. **agents-cli First** — scaffold, build, and deploy with agents-cli
3. **Local Testing First** — `agents-cli run` before deploying
4. **Full Telemetry** — payload logging always enabled
5. **No Stale Entries** — no pinned requirements, generated artifacts are gitignored
6. **Consistent Deploy/Undeploy** — idempotent scripts, full cleanup
7. **Use AI Skills** — leverage installed skills for GCP, ADK, Cloud Run, etc.
8. **Documentation** — README.md + ARCHITECTURE.md for every demo
9. **Demo Guide** — DEMO.md with sample prompts, scenarios, and what to observe
10. **Optional GE Registration** — register agents with Gemini Enterprise Apps via env vars
11. **Unique Agent Names** — no name collisions across demos for safe concurrent deployment
