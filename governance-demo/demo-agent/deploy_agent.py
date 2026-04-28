import json
import os
import sys

import vertexai
from vertexai_genai.agentengines import AgentEngineConfig

from app.agent_runtime_app import agent_runtime


def deploy():
    project_id = os.environ.get("PROJECT_ID")
    if not project_id:
        print("ERROR: PROJECT_ID environment variable is required")
        sys.exit(1)

    location = os.environ.get("REGION", "us-central1")
    gateway_id = os.environ.get("GATEWAY_RESOURCE_ID")
    display_name = os.environ.get("AGENT_DISPLAY_NAME", "demo-agent-governed")
    mcp_server_url = os.environ.get("MCP_SERVER_URL", "")
    gemini_model = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
    logs_bucket = os.environ.get("LOGS_BUCKET_NAME", f"{project_id}-agent-staging")

    print(f"Project: {project_id}, Location: {location}")
    print(f"Gateway: {gateway_id}")

    client = vertexai.Client(
        project=project_id,
        location=location,
        http_options={"api_version": "v1beta1"},
    )

    env_vars = {
        "MCP_SERVER_URL": mcp_server_url,
        "GEMINI_MODEL": gemini_model,
        "LOGS_BUCKET_NAME": logs_bucket,
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "NO_CONTENT",
        "GOOGLE_CLOUD_LOCATION": "global",
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
    }

    config_kwargs = {
        "display_name": display_name,
        "env_vars": env_vars,
        "agent_framework": "google-adk",
    }

    if gateway_id:
        config_kwargs["agent_gateway_config"] = {
            "agent_to_anywhere_config": {
                "agent_gateway": gateway_id
            }
        }
        from vertexai_genai.types import IdentityType
        config_kwargs["identity_type"] = IdentityType.AGENT_IDENTITY

    config = AgentEngineConfig(**config_kwargs)

    existing = [
        a for a in client.agent_engines.list()
        if a.api_resource.display_name == display_name
    ]

    if existing:
        print(f"Updating existing agent: {existing[0].api_resource.name}")
        result = client.agent_engines.update(name=existing[0].api_resource.name, config=config)
    else:
        print("Creating new agent...")
        result = client.agent_engines.create(config=config)

    agent_name = result.api_resource.name if hasattr(result, 'api_resource') else str(result)
    print(f"Deployed: {agent_name}")

    metadata = {
        "remote_agent_runtime_id": agent_name,
        "deployment_target": "agent_runtime",
        "is_a2a": False,
    }
    with open("deployment_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)


if __name__ == "__main__":
    deploy()
