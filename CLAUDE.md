# Agent Platform Demos — Development Standards

## Project Structure

```
ge-agentplatform-demos/
├── setup-project.sh          # One-time project IAM setup (principal sets)
├── _template/                # Reference skeleton for new demos
├── governance-demo/          # Agent Gateway + IAP governance
├── sessions-memory-demo/     # Sessions + Memory Bank
└── <new-demo>/               # Copy from _template/
```

Each demo follows the same directory layout:
```
<demo-name>/
├── .env.template
├── deploy.sh
├── undeploy.sh
└── demo-agent/
    ├── pyproject.toml
    ├── deploy_agent.py
    └── app/
        ├── __init__.py
        ├── agent.py
        ├── agent_runtime_app.py
        └── app_utils/
            ├── telemetry.py
            └── .requirements.txt
```

## Agent Identity

Every deployed agent MUST use SPIFFE identity:

```python
from vertexai._genai.types import IdentityType
config = AgentEngineConfig(
    identityType=IdentityType.AGENT_IDENTITY,
    ...
)
```

Common IAM roles are granted project-wide via **principal sets** in `setup-project.sh`. Individual deploy scripts should NOT grant standard roles per-agent. Only grant demo-specific sensitive roles (e.g., `roles/agentregistry.viewer` for governance demo) per-agent after deployment.

The principal set format for this project:
```
principalSet://agents.global.org-{ORG_ID}.system.id.goog/attribute.platformContainer/aiplatform/projects/{PROJECT_NUMBER}
```

## Telemetry

All demos MUST capture full payloads (prompts, responses, tool calls), not just metadata.

In `deploy_agent.py`:
```python
env_vars = {
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "true",
    "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
    "LOGS_BUCKET_NAME": f"{project_id}-<demo>-staging",
    ...
}
```

In `telemetry.py`: never hard-override `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`. Use `os.environ.setdefault(...)` so the deploy-time value takes precedence. Copy the pattern from `_template/demo-agent/app/app_utils/telemetry.py`.

## Deployment

Use `deploy_agent.py` with `vertexai.Client` directly — NOT `agents-cli deploy`. The CLI does not support all config options (context_spec, agentGatewayConfig, etc.).

Required pattern in `deploy_agent.py`:
- `source_packages=["./app"]` (not cloudpickle)
- `entrypoint_module="app.agent_runtime_app"`
- `entrypoint_object="agent_runtime"`
- `class_methods` generated via `_agent_engines_utils`
- `api_version="v1beta1"` (required for AGENT_IDENTITY)

## Environment Files

- `.env` files are gitignored. Provide `.env.template` with documented defaults.
- Never commit secrets or project-specific values.

## New Demo Checklist

When creating a new demo:
1. Copy `_template/` to `<new-demo-name>/`
2. Update `.env.template` with demo-specific variables
3. Update `deploy.sh` with demo-specific resource creation (MCP servers, registries, etc.)
4. Update `agent.py` with the demo's agent logic
5. Run `setup-project.sh` if not already done on the target project
6. Verify full telemetry appears in Cloud Trace and GCS after deployment
