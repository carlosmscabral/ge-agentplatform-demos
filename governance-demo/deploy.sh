#!/bin/bash
set -e

# Configuration
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)")
REGION="us-central1"
MCP_SERVICE_NAME="finance-mcp-server"
AGENT_NAME="governance_demo_agent"
GATEWAY_NAME="cabral-gateway"
STAGING_BUCKET="gs://${PROJECT_ID}-agent-staging"
RE_SERVICE_ACCOUNT="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

echo "Using Project: ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "Region: ${REGION}"

export MCP_URL=$(gcloud run services describe ${MCP_SERVICE_NAME} --platform managed --region ${REGION} --format='value(status.url)')
export GATEWAY_RESOURCE_ID="projects/${PROJECT_ID}/locations/${REGION}/agentGateways/${GATEWAY_NAME}"

# 6. Deploy ADK Agent to Agent Runtime
echo "Deploying ADK Agent to Agent Runtime..."
cd demo-agent
export MCP_SERVER_URL="${MCP_URL}/sse"
export GOOGLE_CLOUD_LOCATION="global"
export GOOGLE_API_USE_MTLS=never

cat << DEPLOY_EOF > deploy_agent.py
import os
import vertexai
from google.cloud import aiplatform
from vertexai.preview import reasoning_engines
from app.agent_runtime_app import agent_runtime

def deploy():
    project_id = "${PROJECT_ID}"
    location = "${REGION}"
    staging_bucket = "${STAGING_BUCKET}"
    gateway_id = "${GATEWAY_RESOURCE_ID}"
    
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
DEPLOY_EOF

uv run python deploy_agent.py
AGENT_RESOURCE_NAME=$(cat deployed_engine.txt)

# 7. Extract SPIFFE ID
echo "Extracting Agent SPIFFE ID..."
SPIFFE_ID=$(gcloud ai reasoning-engines describe ${AGENT_RESOURCE_NAME} --project=${PROJECT_ID} --location=${REGION} --format='value(agentIdentity.spiffeId)')

echo "Agent Resource: ${AGENT_RESOURCE_NAME}"
echo "Agent SPIFFE ID: ${SPIFFE_ID}"
cd ..

# 8. Set IAM Deny Policy (Agent Gateway MCP Enforcement)
echo "Applying IAM Deny Policy to enforce read-only tools via Agent Gateway..."

cat << POLICY_EOF > mcp-deny-policy.json
{
  "rules": [
    {
      "denyRule": {
        "deniedPrincipals": [
          "principalSet://goog/public:all"
        ],
        "deniedPermissions": [
          "mcp.googleapis.com/tools.call"
        ],
        "denialCondition": {
          "title": "Deny read-write tools",
          "expression": "api.getAttribute('mcp.googleapis.com/tool.isReadOnly', false) == false"
        }
      }
    }
  ]
}
POLICY_EOF

gcloud iam policies delete mcp-read-only-policy \
  --attachment-point=cloudresourcemanager.googleapis.com/projects/${PROJECT_ID} \
  --kind=denypolicies --quiet || true

gcloud iam policies create mcp-read-only-policy \
  --attachment-point=cloudresourcemanager.googleapis.com/projects/${PROJECT_ID} \
  --kind=denypolicies \
  --policy-file=mcp-deny-policy.json

echo "Deployment and Policy setup complete."
