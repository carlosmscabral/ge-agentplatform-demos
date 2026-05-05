"""Deploy the sessions-memory demo agent to Agent Runtime with Memory Bank.

Uses vertexai.Client directly because agents-cli deploy does not support
context_spec (required for Memory Bank topic configuration).

Usage:
    PROJECT_ID=vibe-cabral REGION=us-central1 uv run python deploy_agent.py
"""

import json
import os
import sys

import vertexai
from vertexai._genai.types import (
    AgentEngineConfig,
    IdentityType,
    ReasoningEngineContextSpec,
)

from vertexai._genai import _agent_engines_utils

from app.agent_runtime_app import agent_runtime
from app.memory_config import memory_bank_config


def deploy():
    project_id = os.environ.get("PROJECT_ID")
    if not project_id:
        print("ERROR: PROJECT_ID environment variable is required")
        sys.exit(1)

    location = os.environ.get("REGION", "us-central1")
    display_name = os.environ.get("AGENT_DISPLAY_NAME", "sessions-memory-demo")
    gemini_model = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
    logs_bucket = os.environ.get("LOGS_BUCKET_NAME", f"{project_id}-sessions-demo-staging")

    print(f"Project:  {project_id}")
    print(f"Location: {location}")
    print(f"Display:  {display_name}")

    client = vertexai.Client(
        project=project_id,
        location=location,
        http_options={"api_version": "v1beta1"},
    )

    env_vars = {
        "GEMINI_MODEL": gemini_model,
        "LOGS_BUCKET_NAME": logs_bucket,
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "NO_CONTENT",
        "GOOGLE_CLOUD_LOCATION": "global",
        "GOOGLE_CLOUD_REGION": location,
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
    }

    registered_ops = _agent_engines_utils._get_registered_operations(agent=agent_runtime)
    class_methods_spec = _agent_engines_utils._generate_class_methods_spec_or_raise(
        agent=agent_runtime, operations=registered_ops,
    )
    class_methods = [_agent_engines_utils._to_dict(m) for m in class_methods_spec]

    context_spec = ReasoningEngineContextSpec(
        memory_bank_config=memory_bank_config,
    )

    config = AgentEngineConfig(
        displayName=display_name,
        stagingBucket=f"gs://{project_id}-sessions-demo-staging",
        envVars=env_vars,
        agentFramework="google-adk",
        identityType=IdentityType.AGENT_IDENTITY,
        contextSpec=context_spec,
        source_packages=["./app"],
        entrypoint_module="app.agent_runtime_app",
        entrypoint_object="agent_runtime",
        requirements_file="app/app_utils/.requirements.txt",
        class_methods=class_methods,
    )

    existing = [
        a for a in client.agent_engines.list()
        if a.api_resource.display_name == display_name
    ]

    if existing:
        print(f"Updating existing agent: {existing[0].api_resource.name}")
        agent = client.agent_engines.update(
            name=existing[0].api_resource.name,
            config=config,
        )
    else:
        print("Creating new agent...")
        agent = client.agent_engines.create(config=config)

    name = agent.api_resource.name
    print(f"Deployed: {name}")

    metadata = {
        "remote_agent_runtime_id": name,
        "deployment_target": "agent_runtime",
        "is_a2a": False,
    }
    with open("deployment_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    parts = name.split("/")
    engine_id = parts[-1]
    project_number = parts[1]

    print(f"\nPlayground: https://console.cloud.google.com/vertex-ai/agents/agent-engines/"
          f"locations/{location}/agent-engines/{engine_id}/playground?project={project_id}")


if __name__ == "__main__":
    deploy()
