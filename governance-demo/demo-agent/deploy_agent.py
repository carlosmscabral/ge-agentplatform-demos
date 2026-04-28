import os
import vertexai
from google.cloud import aiplatform
from vertexai.preview import reasoning_engines
from app.agent_runtime_app import agent_runtime

def deploy():
    project_id = "vibe-cabral"
    location = "us-central1"
    staging_bucket = "gs://vibe-cabral-agent-staging"
    gateway_id = "projects/vibe-cabral/locations/us-central1/agentGateways/cabral-gateway"
    
    print(f"Deploying agent with Gateway: {gateway_id}")
    vertexai.init(project=project_id, location=location, staging_bucket=staging_bucket)

    remote_agent = reasoning_engines.ReasoningEngine.create(
        agent_runtime,
        display_name="demo-agent-governed",
        requirements=[
            "google-adk>=1.28.0,<2.0.0",
            "mcp[cli]>=1.3.0,<2.0.0",
            "opentelemetry-instrumentation-fastapi>=0.46b0",
            "opentelemetry-instrumentation-grpc>=0.46b0",
            "opentelemetry-instrumentation-httpx>=0.46b0"
        ],
        extra_packages=["app"],
        sys_version="3.11"
    )
    print(f"Deployed: {remote_agent.resource_name}")
    
    # Save the resource name for the next step
    with open("deployed_engine.txt", "w") as f:
        f.write(remote_agent.resource_name)

if __name__ == "__main__":
    deploy()
