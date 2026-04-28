#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Load Configuration ─────────────────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a; source "${SCRIPT_DIR}/.env"; set +a
fi

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
PROJECT_NUMBER="${PROJECT_NUMBER:-$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')}"
REGION="${REGION:-us-central1}"
MCP_SERVICE_NAME="${MCP_SERVICE_NAME:-finance-mcp-server}"
GATEWAY_NAME="${GATEWAY_NAME:-demo-agent-gateway}"
AGENT_REGISTRY_SERVICE_NAME="${AGENT_REGISTRY_SERVICE_NAME:-finance-mcp-service}"
AGENT_DISPLAY_NAME="${AGENT_DISPLAY_NAME:-demo-agent-governed}"
STAGING_BUCKET="${STAGING_BUCKET:-gs://${PROJECT_ID}-agent-staging}"
DENY_POLICY_NAME="${DENY_POLICY_NAME:-mcp-read-only-policy}"
RE_SERVICE_ACCOUNT="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

export PROJECT_ID REGION GATEWAY_NAME

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         Agent Platform Governance Demo — Deploy             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:  ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "  Region:   ${REGION}"
echo "  Gateway:  ${GATEWAY_NAME}"
echo ""

# ─── Step 1: Build & Deploy MCP Server to Cloud Run ─────────────────────────
echo ">>> Step 1/10: Deploying MCP server to Cloud Run..."
gcloud run deploy "${MCP_SERVICE_NAME}" \
    --source="${SCRIPT_DIR}/mcp-server" \
    --region="${REGION}" \
    --allow-unauthenticated \
    --port=8080 \
    --quiet

MCP_URL=$(gcloud run services describe "${MCP_SERVICE_NAME}" \
    --platform managed --region "${REGION}" \
    --format='value(status.url)')
echo "    MCP Server URL: ${MCP_URL}"

# ─── Step 2: Create Staging GCS Bucket ───────────────────────────────────────
echo ""
echo ">>> Step 2/10: Creating staging bucket ${STAGING_BUCKET}..."
gcloud storage buckets create "${STAGING_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --quiet 2>/dev/null || echo "    Bucket already exists."

# ─── Step 3: Register MCP Server in Agent Registry ──────────────────────────
echo ""
echo ">>> Step 3/10: Registering MCP server in Agent Registry..."
gcloud alpha agent-registry services delete "${AGENT_REGISTRY_SERVICE_NAME}" \
    --location="${REGION}" --quiet 2>/dev/null || true

TOOLSPEC_CONTENT=$(cat "${SCRIPT_DIR}/toolspec.json")
gcloud alpha agent-registry services create "${AGENT_REGISTRY_SERVICE_NAME}" \
    --location="${REGION}" \
    --display-name="Finance MCP Server" \
    --interfaces="protocolBinding=jsonrpc,url=${MCP_URL}/sse" \
    --mcp-server-spec-type=tool-spec \
    --mcp-server-spec-content="${TOOLSPEC_CONTENT}"
echo "    Registered as: ${AGENT_REGISTRY_SERVICE_NAME}"

# ─── Step 4: Create Agent Gateway ───────────────────────────────────────────
echo ""
echo ">>> Step 4/10: Creating Agent Gateway..."
envsubst < "${SCRIPT_DIR}/gateway.yaml.template" > "/tmp/gateway-rendered.yaml"

gcloud alpha network-services agent-gateways import "${GATEWAY_NAME}" \
    --source="/tmp/gateway-rendered.yaml" \
    --location="${REGION}" \
    --quiet 2>/dev/null || echo "    Gateway already exists or updated."

GATEWAY_RESOURCE_ID="projects/${PROJECT_ID}/locations/${REGION}/agentGateways/${GATEWAY_NAME}"
echo "    Gateway: ${GATEWAY_RESOURCE_ID}"

# ─── Step 5: Create IAP Service Extension ───────────────────────────────────
echo ""
echo ">>> Step 5/10: Creating IAP service extension..."
gcloud service-extensions authz-extensions import iap-extension \
    --source="${SCRIPT_DIR}/iap-extension.yaml" \
    --location="${REGION}" \
    --quiet 2>/dev/null || echo "    IAP extension already exists."

# ─── Step 6: Authorization Policy (skipped for Google-managed gateways) ─────
echo ""
echo ">>> Step 6/10: Authorization policy..."
echo "    Skipped: Google-managed gateways use IAM Deny Policies for tool governance."
echo "    (Authz policies via network-security require self-managed gateways.)"

# ─── Step 7: Grant IAM Roles to RE Service Account ─────────────────────────
echo ""
echo ">>> Step 7/10: Granting IAM roles to Reasoning Engine service account..."
for ROLE in roles/cloudtrace.agent roles/storage.objectAdmin; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${RE_SERVICE_ACCOUNT}" \
        --role="${ROLE}" \
        --condition=None --quiet > /dev/null 2>&1 || true
    echo "    Granted ${ROLE}"
done

# ─── Step 8: Deploy ADK Agent with Gateway Config ──────────────────────────
echo ""
echo ">>> Step 8/10: Deploying ADK Agent to Agent Runtime via agents-cli..."
cd "${SCRIPT_DIR}/demo-agent"

agents-cli deploy \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --agent-identity \
    --update-env-vars "MCP_SERVER_URL=${MCP_URL}/sse,GEMINI_MODEL=${GEMINI_MODEL:-gemini-3-flash-preview},LOGS_BUCKET_NAME=${PROJECT_ID}-agent-staging,OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT,GOOGLE_CLOUD_LOCATION=global" \
    --no-confirm-project

AGENT_RESOURCE_NAME=$(python3 -c "import json; print(json.load(open('deployment_metadata.json'))['remote_agent_runtime_id'])")
cd "${SCRIPT_DIR}"

# ─── Step 9: Extract SPIFFE ID ─────────────────────────────────────────────
echo ""
echo ">>> Step 9/10: Extracting Agent SPIFFE ID..."
SPIFFE_ID=$(python3 -c "
import json
meta = json.load(open('${SCRIPT_DIR}/demo-agent/deployment_metadata.json'))
print(meta.get('spiffe_id', '(not available)'))
") || SPIFFE_ID="(not available)"

echo "    Agent Resource: ${AGENT_RESOURCE_NAME}"
echo "    Agent SPIFFE ID: ${SPIFFE_ID}"

# ─── Step 10: Apply IAM Deny Policy ────────────────────────────────────────
echo ""
echo ">>> Step 10/10: Applying IAM Deny Policy for read-only tool enforcement..."
gcloud iam policies delete "${DENY_POLICY_NAME}" \
    --attachment-point="cloudresourcemanager.googleapis.com/projects/${PROJECT_ID}" \
    --kind=denypolicies --quiet 2>/dev/null || true

gcloud iam policies create "${DENY_POLICY_NAME}" \
    --attachment-point="cloudresourcemanager.googleapis.com/projects/${PROJECT_ID}" \
    --kind=denypolicies \
    --policy-file="${SCRIPT_DIR}/mcp-deny-policy.json"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Deployment Complete                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Agent:    ${AGENT_RESOURCE_NAME}"
echo "║  SPIFFE:   ${SPIFFE_ID}"
echo "║  MCP:      ${MCP_URL}"
echo "║  Gateway:  ${GATEWAY_RESOURCE_ID}"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Test with:"
echo "  cd demo-agent && PROJECT_ID=${PROJECT_ID} REGION=${REGION} uv run python test_deployed_agent.py"
