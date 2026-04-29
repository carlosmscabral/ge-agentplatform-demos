#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Load Configuration ─────────────────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a; source "${SCRIPT_DIR}/.env"; set +a
fi

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
MCP_SERVICE_NAME="${MCP_SERVICE_NAME:-finance-mcp-server}"
GATEWAY_NAME="${GATEWAY_NAME:-demo-agent-gateway}"
AGENT_REGISTRY_SERVICE_NAME="${AGENT_REGISTRY_SERVICE_NAME:-finance-mcp-service}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         Agent Platform Governance Demo — Teardown           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:  ${PROJECT_ID}"
echo "  Region:   ${REGION}"
echo ""

# ─── Step 1: Delete IAP Allow Policy (if applied) ─────────────────────────
echo ">>> Step 1/7: IAP Allow Policy cleanup..."
echo "    If an IAP allow policy was applied, remove it manually:"
echo "    gcloud beta iap web remove-iam-policy-binding ... --project=${PROJECT_ID}"

# ─── Step 2: Delete ADK Agent ──────────────────────────────────────────────
echo ""
echo ">>> Step 2/7: Deleting ADK Agent from Agent Runtime..."
if [ -f "${SCRIPT_DIR}/demo-agent/deployment_metadata.json" ]; then
    AGENT_RESOURCE_NAME=$(python3 -c "import json; print(json.load(open('${SCRIPT_DIR}/demo-agent/deployment_metadata.json'))['remote_agent_runtime_id'])")
    RE_ID=$(echo "${AGENT_RESOURCE_NAME}" | grep -oP 'reasoningEngines/\K[0-9]+')
    PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
    ACCESS_TOKEN=$(gcloud auth print-access-token)
    curl -s -X DELETE \
        "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines/${RE_ID}?force=true" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" || echo "    Agent not found."
    command rm -f "${SCRIPT_DIR}/demo-agent/deployment_metadata.json"
else
    echo "    No deployment artifact found, skipping agent deletion."
fi

# ─── Step 3: Delete Authorization Policy ───────────────────────────────────
echo ""
echo ">>> Step 3/7: Deleting authorization policy..."
gcloud network-security authz-policies delete mcp-authz-policy \
    --location="${REGION}" --quiet 2>/dev/null || echo "    Authz policy not found."

# ─── Step 4: Delete IAP Service Extension ──────────────────────────────────
echo ""
echo ">>> Step 4/7: Deleting IAP service extension..."
gcloud service-extensions authz-extensions delete iap-extension \
    --location="${REGION}" --quiet 2>/dev/null || echo "    IAP extension not found."

# ─── Step 5: Delete Agent Gateway ──────────────────────────────────────────
echo ""
echo ">>> Step 5/7: Deleting Agent Gateway..."
gcloud alpha network-services agent-gateways delete "${GATEWAY_NAME}" \
    --location="${REGION}" --quiet 2>/dev/null || echo "    Gateway not found."

# ─── Step 6: Unregister from Agent Registry ────────────────────────────────
echo ""
echo ">>> Step 6/7: Unregistering MCP server from Agent Registry..."
gcloud alpha agent-registry services delete "${AGENT_REGISTRY_SERVICE_NAME}" \
    --location="${REGION}" --quiet 2>/dev/null || echo "    Registry entry not found."

# ─── Step 7: Delete MCP Server from Cloud Run ─────────────────────────────
echo ""
echo ">>> Step 7/7: Deleting MCP Server from Cloud Run..."
gcloud run services delete "${MCP_SERVICE_NAME}" \
    --platform managed --region "${REGION}" --quiet \
    || echo "    MCP service not found."

# Uncomment to also delete the staging bucket:
# echo ""
# echo ">>> Deleting staging bucket..."
# gcloud storage rm --recursive "${STAGING_BUCKET:-gs://${PROJECT_ID}-agent-staging}" || true

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                   Teardown Complete                         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
