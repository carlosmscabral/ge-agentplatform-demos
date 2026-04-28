#!/bin/bash
# Configuration
PROJECT_ID=$(gcloud config get-value project)
REGION="us-central1"
MCP_SERVICE_NAME="finance-mcp-server"

echo "Using Project: ${PROJECT_ID}"
echo "Region: ${REGION}"

# 1. Delete IAM Deny Policy
echo "Deleting IAM Deny Policy..."
gcloud iam policies delete mcp-read-only-policy \
  --attachment-point=cloudresourcemanager.googleapis.com/projects/${PROJECT_ID} \
  --kind=denypolicies --quiet || echo "Deny policy not found."

# 2. Delete ADK Agent
echo "Deleting ADK Agent from Agent Runtime..."
if [ -f "demo-agent/deployed_engine.txt" ]; then
    AGENT_RESOURCE_NAME=$(cat demo-agent/deployed_engine.txt)
    gcloud ai reasoning-engines delete ${AGENT_RESOURCE_NAME} --quiet || echo "Agent not found."
elif [ -f "demo-agent/deployment_metadata.json" ]; then
    AGENT_RESOURCE_NAME=$(cat demo-agent/deployment_metadata.json | python3 -c "import sys, json; print(json.load(sys.stdin)['remote_agent_runtime_id'])")
    gcloud ai reasoning-engines delete ${AGENT_RESOURCE_NAME} --project=${PROJECT_ID} --location=${REGION} --quiet || echo "Agent not found."
else
    echo "deployment files not found, skipping agent deletion."
fi

# 3. Delete MCP Server from Cloud Run
echo "Deleting MCP Server from Cloud Run..."
gcloud run services delete ${MCP_SERVICE_NAME} --platform managed --region ${REGION} --quiet || echo "MCP service not found."

# 4. Clean up Gateway / Registry manually (Optional, add if needed)
echo "Undeploy complete."
