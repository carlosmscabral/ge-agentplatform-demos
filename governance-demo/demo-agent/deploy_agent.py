"""Deploy the governance demo agent to Agent Runtime.

Single-call deployment that supports Agent Gateway attachment,
agent identity (SPIFFE), and observability configuration.

Usage:
    # Without gateway
    PROJECT_ID=my-project REGION=us-central1 \
        MCP_SERVER_URL=https://my-mcp.run.app/mcp \
        uv run python deploy_agent.py

    # With gateway
    PROJECT_ID=my-project REGION=us-central1 \
        MCP_SERVER_URL=https://my-mcp.run.app/mcp \
        AGENT_GATEWAY_RESOURCE_ID=projects/my-project/locations/us-central1/agentGateways/my-gw \
        uv run python deploy_agent.py
"""

import json
import os
import sys

import vertexai
from vertexai._genai.types.common import AgentEngineConfig, IdentityType

from app.agent_runtime_app import agent_runtime


def deploy():
    project_id = os.environ.get("PROJECT_ID")
    if not project_id:
        print("ERROR: PROJECT_ID environment variable is required")
        sys.exit(1)

    location = os.environ.get("REGION", "us-central1")
    gateway_id = os.environ.get("AGENT_GATEWAY_RESOURCE_ID")
    display_name = os.environ.get("AGENT_DISPLAY_NAME", "demo-agent-governed")
    mcp_server_url = os.environ.get("MCP_SERVER_URL", "")
    gemini_model = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
    logs_bucket = os.environ.get("LOGS_BUCKET_NAME", f"{project_id}-agent-staging")

    print(f"Project:  {project_id}")
    print(f"Location: {location}")
    print(f"Gateway:  {gateway_id or '(none)'}")

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
        "displayName": display_name,
        "stagingBucket": f"gs://{project_id}-agent-staging",
        "envVars": env_vars,
        "agentFramework": "google-adk",
        "identityType": IdentityType.AGENT_IDENTITY,
    }

    if gateway_id:
        config_kwargs["agentGatewayConfig"] = {
            "agentToAnywhereConfig": {
                "agentGateway": gateway_id
            }
        }

    config = AgentEngineConfig(**config_kwargs)

    existing = [
        a for a in client.agent_engines.list()
        if a.api_resource.display_name == display_name
    ]
    if existing:
        print(f"ERROR: Agent '{display_name}' already exists: {existing[0].api_resource.name}")
        print("Delete it first (gateway can only be set at creation time).")
        sys.exit(1)

    print("Creating agent...")
    agent = client.agent_engines.create(agent=agent_runtime, config=config)

    name = agent.api_resource.name
    print(f"Deployed: {name}")

    metadata = {
        "remote_agent_runtime_id": name,
        "deployment_target": "agent_runtime",
        "is_a2a": False,
        "gateway": gateway_id,
    }
    with open("deployment_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    spiffe = getattr(agent.api_resource, "agent_identity", None)
    if spiffe:
        print(f"SPIFFE: {spiffe}")

    print(f"\nPlayground: https://console.cloud.google.com/vertex-ai/agents/agent-engines/"
          f"locations/{location}/agent-engines/{name.split('/')[-1]}/playground?project={project_id}")


if __name__ == "__main__":
    deploy()
