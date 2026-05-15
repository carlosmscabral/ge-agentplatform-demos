#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Load Configuration ─────────────────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a; source "${SCRIPT_DIR}/.env"; set +a
fi

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
STAGING_BUCKET="${STAGING_BUCKET:-${PROJECT_ID}-a2a-demo-staging}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3-flash-preview}"
SPECIALIST_DISPLAY_NAME="${SPECIALIST_DISPLAY_NAME:-a2a-demo-specialist}"
ORCHESTRATOR_DISPLAY_NAME="${ORCHESTRATOR_DISPLAY_NAME:-a2a-demo-orchestrator}"

export PROJECT_ID REGION

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          Agent Platform A2A Demo — Deploy                   ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:     ${PROJECT_ID}"
echo "  Region:      ${REGION}"
echo "  Model:       ${GEMINI_MODEL}"
echo "  Specialist:  ${SPECIALIST_DISPLAY_NAME}"
echo "  Orchestrator: ${ORCHESTRATOR_DISPLAY_NAME}"
echo ""

# ─── Step 1: Create Staging GCS Bucket ───────────────────────────────────────
echo ">>> Step 1/4: Creating staging bucket gs://${STAGING_BUCKET}..."
gcloud storage buckets create "gs://${STAGING_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --quiet 2>/dev/null || echo "    Bucket already exists."

# ─── Step 2: Deploy Specialist Agent (A2A server) ───────────────────────────
echo ""
echo ">>> Step 2/4: Deploying Specialist Agent (A2A)..."
cd "${SCRIPT_DIR}/specialist-agent"

agents-cli deploy \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --update-env-vars "GEMINI_MODEL=${GEMINI_MODEL},GOOGLE_CLOUD_LOCATION=global,LOGS_BUCKET_NAME=${STAGING_BUCKET},OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY,OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false" \
    --no-confirm-project

SPECIALIST_RESOURCE=$(python3 -c "import json; print(json.load(open('deployment_metadata.json'))['remote_agent_runtime_id'])")
SPECIALIST_RE_ID=$(echo "${SPECIALIST_RESOURCE}" | grep -oP 'reasoningEngines/\K[0-9]+')
SPECIALIST_URL="https://${REGION}-aiplatform.googleapis.com/v1beta1/${SPECIALIST_RESOURCE}"

echo ""
echo "  Specialist deployed: ${SPECIALIST_RESOURCE}"
echo "  Specialist URL:      ${SPECIALIST_URL}"

cd "${SCRIPT_DIR}"

# ─── Step 3: Discover A2A Card URL ──────────────────────────────────────────
echo ""
echo ">>> Step 3/4: Discovering A2A agent card URL..."

# Agent Runtime serves A2A cards at /a2a/v1/card (not /.well-known/agent.json)
SPECIALIST_A2A_CARD_URL="${SPECIALIST_URL}/a2a/v1/card"
echo "  Trying: ${SPECIALIST_A2A_CARD_URL}"

ACCESS_TOKEN=$(gcloud auth print-access-token)
CARD_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
    "${SPECIALIST_A2A_CARD_URL}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" 2>/dev/null || echo "000")

if [ "${CARD_RESPONSE}" = "200" ]; then
    echo "  ✓ Agent card accessible at ${SPECIALIST_A2A_CARD_URL}"
else
    echo "  ✗ Agent card NOT accessible (HTTP ${CARD_RESPONSE})"
    echo "  ERROR: Cannot discover specialist A2A card. Orchestrator may not be able to delegate."
    echo "  Falling back to base URL: ${SPECIALIST_URL}"
    SPECIALIST_A2A_CARD_URL="${SPECIALIST_URL}"
fi

# ─── Step 4: Deploy Orchestrator Agent (A2A client) ─────────────────────────
echo ""
echo ">>> Step 4/4: Deploying Orchestrator Agent with specialist URL..."
cd "${SCRIPT_DIR}/orchestrator-agent"

agents-cli deploy \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --update-env-vars "GEMINI_MODEL=${GEMINI_MODEL},SPECIALIST_A2A_CARD_URL=${SPECIALIST_A2A_CARD_URL},GOOGLE_CLOUD_LOCATION=global,LOGS_BUCKET_NAME=${STAGING_BUCKET},OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY,OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false" \
    --no-confirm-project

ORCHESTRATOR_RESOURCE=$(python3 -c "import json; print(json.load(open('deployment_metadata.json'))['remote_agent_runtime_id'])")
ORCHESTRATOR_URL="https://${REGION}-aiplatform.googleapis.com/v1beta1/${ORCHESTRATOR_RESOURCE}"

cd "${SCRIPT_DIR}"

# ─── Optional: Register with Gemini Enterprise ──────────────────────────────
if [ -n "${GEMINI_ENTERPRISE_APP_ID:-}" ]; then
    echo ""
    echo ">>> Registering with Gemini Enterprise..."
    cd "${SCRIPT_DIR}/orchestrator-agent"
    agents-cli publish gemini-enterprise \
        --gemini-enterprise-app-id "${GEMINI_ENTERPRISE_APP_ID}" \
        --display-name "${GEMINI_DISPLAY_NAME:-${ORCHESTRATOR_DISPLAY_NAME}}" \
        --description "${GEMINI_DESCRIPTION:-A2A orchestrator demo agent}" \
        --no-confirm-project \
        || echo "    GE registration failed (non-blocking)."
    cd "${SCRIPT_DIR}"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Deployment Complete                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Specialist:   ${SPECIALIST_RESOURCE}"
echo "║  Orchestrator: ${ORCHESTRATOR_RESOURCE}"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Test specialist (A2A mode):"
echo "  cd specialist-agent && agents-cli run --url '${SPECIALIST_URL}' --mode a2a 'Converta 100 USD para BRL'"
echo ""
echo "Test orchestrator (ADK mode):"
echo "  cd orchestrator-agent && agents-cli run --url '${ORCHESTRATOR_URL}' --mode adk 'Qual a cotação do dólar hoje?'"
