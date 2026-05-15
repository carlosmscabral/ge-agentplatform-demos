"""Deploy mortgage agent using source_packages strategy (proven pattern).

Uses the same approach as governance-demo: source_packages + entrypoint_module
+ class_methods, instead of cloudpickle.
"""

import json
import os
import sys

import vertexai
from vertexai._genai import _agent_engines_utils
from vertexai._genai.types.common import AgentEngineConfig, IdentityType


def main():
    project_id = os.environ.get("PROJECT_ID", "vibe-cabral")
    region = os.environ.get("REGION", "us-central1")
    gateway_id = os.environ.get("AGENT_GATEWAY_RESOURCE_ID")
    display_name = os.environ.get("AGENT_DISPLAY_NAME", "Mortgage Assistant Agent")
    model = os.environ.get("MODEL_NAME", "gemini-3.1-flash-lite-preview")
    model_endpoint_location = os.environ.get("MODEL_ENDPOINT_LOCATION", "global")
    staging_bucket = f"gs://{project_id}-staging"

    # Allow CLI overrides
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=project_id)
    parser.add_argument("--region", default=region)
    parser.add_argument("--agent-gateway", default=gateway_id)
    parser.add_argument("--model", default=model)
    parser.add_argument("--model-endpoint-location", default=model_endpoint_location)
    parser.add_argument("--display-name", default=display_name)
    parser.add_argument("--staging-bucket", default=staging_bucket)
    args = parser.parse_args()

    project_id = args.project
    region = args.region
    gateway_id = args.agent_gateway
    display_name = args.display_name
    staging_bucket = args.staging_bucket

    print(f"Project:        {project_id}")
    print(f"Region:         {region}")
    print(f"Gateway:        {gateway_id or '(none)'}")
    print(f"Model:          {args.model}")
    print(f"Model endpoint: {args.model_endpoint_location}")
    print(f"Display name:   {display_name}")
    print(f"Staging bucket: {staging_bucket}")
    print()

    # Set env vars needed by agent module at import time
    os.environ["MODEL_NAME"] = args.model
    os.environ["MCP_REGISTRY_PROJECT"] = project_id
    os.environ["MCP_REGISTRY_LOCATION"] = region

    client = vertexai.Client(
        project=project_id,
        location=region,
        http_options={"api_version": "v1beta1"},
    )

    from agent.agent_runtime_app import agent_runtime

    registered_ops = _agent_engines_utils._get_registered_operations(agent=agent_runtime)
    class_methods_spec = _agent_engines_utils._generate_class_methods_spec_or_raise(
        agent=agent_runtime, operations=registered_ops,
    )
    class_methods = [_agent_engines_utils._to_dict(m) for m in class_methods_spec]

    env_vars = {
        "MODEL_NAME": args.model,
        "MCP_REGISTRY_PROJECT": project_id,
        "MCP_REGISTRY_LOCATION": region,
        "GOOGLE_CLOUD_LOCATION": args.model_endpoint_location,
        "GOOGLE_GENAI_USE_VERTEXAI": "True",
        "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "true",
        "OTEL_TRACES_SAMPLER": "parentbased_traceidratio",
        "OTEL_TRACES_SAMPLER_ARG": "1.0",
    }

    config_kwargs = {
        "displayName": display_name,
        "stagingBucket": staging_bucket,
        "envVars": env_vars,
        "agentFramework": "google-adk",
        "identityType": IdentityType.AGENT_IDENTITY,
        "source_packages": ["./agent"],
        "entrypoint_module": "agent.agent_runtime_app",
        "entrypoint_object": "agent_runtime",
        "requirements_file": "agent/.requirements.txt",
        "class_methods": class_methods,
    }

    if gateway_id:
        config_kwargs["agentGatewayConfig"] = {
            "agentToAnywhereConfig": {"agentGateway": gateway_id}
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
    agent = client.agent_engines.create(config=config)

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

    engine_id = name.split("/")[-1]
    print(f"\nPlayground: https://console.cloud.google.com/vertex-ai/agents/agent-engines/"
          f"locations/{region}/agent-engines/{engine_id}/playground?project={project_id}")


if __name__ == "__main__":
    main()
