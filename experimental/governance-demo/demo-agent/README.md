# demo-agent

ADK agent for the Agent Platform Governance Demo. Uses Agent Registry for MCP endpoint discovery and deploys to Agent Runtime with SPIFFE identity.

Generated with `agents-cli` version `0.0.1a1`, then customized for governance.

## Project Structure

```
demo-agent/
├── app/         # Core agent code
│   ├── agent.py               # Main agent logic + _LazyToolset wrapper
│   ├── agent_runtime_app.py   # Agent Runtime application logic
│   └── app_utils/             # Telemetry and typing helpers
├── tests/                     # Unit and integration tests
├── deploy_agent.py            # Standalone deploy with gateway config
├── GEMINI.md                  # AI-assisted development guide
└── pyproject.toml             # Project dependencies
```

## Requirements

- **uv**: Python package manager - [Install](https://docs.astral.sh/uv/getting-started/installation/)
- **agents-cli**: ADK development CLI - Install with `uv tool install agents-cli`
- **Google Cloud SDK**: For GCP services - [Install](https://cloud.google.com/sdk/docs/install)

## Quick Start

```bash
agents-cli install && agents-cli dev
```

## Commands

| Command              | Description                                    |
| -------------------- | ---------------------------------------------- |
| `agents-cli install` | Install dependencies using uv                  |
| `agents-cli dev`     | Launch local development environment           |
| `agents-cli lint`    | Run code quality checks                        |
| `agents-cli test`    | Run unit and integration tests                 |
| `agents-cli deploy`  | Deploy agent to Agent Runtime                  |

## Deployment

For standard deployment (no gateway):
```bash
gcloud config set project <your-project-id>
agents-cli deploy --agent-identity --no-confirm-project
```

For deployment with Agent Gateway attachment, use `deploy_agent.py` — see `../README.md` for details.
