#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Load Configuration ─────────────────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a; source "${SCRIPT_DIR}/.env"; set +a
fi

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
STAGING_BUCKET="${STAGING_BUCKET:-gs://${PROJECT_ID}-sessions-demo-staging}"

export PROJECT_ID REGION STAGING_BUCKET

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║     Agent Platform Sessions & Memory Demo — Deploy          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:  ${PROJECT_ID}"
echo "  Region:   ${REGION}"
echo ""

# ─── Step 1: Create Staging GCS Bucket ───────────────────────────────────────
echo ">>> Step 1/2: Creating staging bucket ${STAGING_BUCKET}..."
gcloud storage buckets create "${STAGING_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --quiet 2>/dev/null || echo "    Bucket already exists."

# ─── Step 2: Deploy Agent with Memory Bank via deploy_agent.py ──────────────
# agents-cli deploy does not support context_spec (required for Memory Bank).
# deploy_agent.py uses vertexai.Client directly to pass ReasoningEngineContextSpec
# with memory_bank_config, source_packages, and class_methods.
echo ""
echo ">>> Step 2/2: Deploying Agent with Memory Bank config..."
cd "${SCRIPT_DIR}/demo-agent"

uv run python deploy_agent.py

AGENT_RESOURCE_NAME=$(python3 -c "import json; print(json.load(open('deployment_metadata.json'))['remote_agent_runtime_id'])")
cd "${SCRIPT_DIR}"

RE_ID=$(echo "${AGENT_RESOURCE_NAME}" | grep -oP 'reasoningEngines/\K[0-9]+')
AGENT_URL="https://${REGION}-aiplatform.googleapis.com/v1beta1/${AGENT_RESOURCE_NAME}"

# ─── Step 3 (optional): Register with Gemini Enterprise ─────────────────────
if [ -n "${GEMINI_ENTERPRISE_APP_ID:-}" ]; then
    echo ""
    echo ">>> Registering with Gemini Enterprise..."
    cd "${SCRIPT_DIR}/demo-agent"
    agents-cli publish gemini-enterprise \
        --gemini-enterprise-app-id "${GEMINI_ENTERPRISE_APP_ID}" \
        --display-name "${GEMINI_DISPLAY_NAME:-${AGENT_DISPLAY_NAME:-sessions-memory-demo}}" \
        --description "${GEMINI_DESCRIPTION:-Sessions and Memory Bank demo agent}" \
        --no-confirm-project \
        || echo "    GE registration failed (non-blocking)."
    cd "${SCRIPT_DIR}"
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Deployment Complete                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Agent:  ${AGENT_RESOURCE_NAME}"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Test with:"
echo "  cd demo-agent && agents-cli run --url '${AGENT_URL}' --mode adk 'Look up account cust_001'"
echo ""
echo "Run the demo scripts:"
echo "  cd demo-agent && uv run python ../scripts/demo_stateless.py"
echo "  cd demo-agent && uv run python ../scripts/demo_stateful.py"
