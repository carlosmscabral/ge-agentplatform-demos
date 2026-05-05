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
STAGING_BUCKET="${STAGING_BUCKET:-gs://${PROJECT_ID}-sessions-demo-staging}"
RE_SERVICE_ACCOUNT="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

export PROJECT_ID REGION

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║     Agent Platform Sessions & Memory Demo — Deploy          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:  ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "  Region:   ${REGION}"
echo ""

# ─── Step 1: Create Staging GCS Bucket ───────────────────────────────────────
echo ">>> Step 1/3: Creating staging bucket ${STAGING_BUCKET}..."
gcloud storage buckets create "${STAGING_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --quiet 2>/dev/null || echo "    Bucket already exists."

# ─── Step 2: Grant IAM Roles to RE Service Account ─────────────────────────
echo ""
echo ">>> Step 2/3: Granting IAM roles to Reasoning Engine service account..."
for ROLE in roles/cloudtrace.agent roles/storage.objectAdmin; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${RE_SERVICE_ACCOUNT}" \
        --role="${ROLE}" \
        --condition=None --quiet > /dev/null 2>&1 || true
    echo "    Granted ${ROLE}"
done

# ─── Step 3: Deploy Agent with Memory Bank via deploy_agent.py ──────────────
# agents-cli deploy does not support context_spec (required for Memory Bank).
# deploy_agent.py uses vertexai.Client directly to pass ReasoningEngineContextSpec
# with memory_bank_config, source_packages, and class_methods.
echo ""
echo ">>> Step 3/3: Deploying Agent with Memory Bank config..."
cd "${SCRIPT_DIR}/demo-agent"

uv run python deploy_agent.py

AGENT_RESOURCE_NAME=$(python3 -c "import json; print(json.load(open('deployment_metadata.json'))['remote_agent_runtime_id'])")
cd "${SCRIPT_DIR}"

RE_ID=$(echo "${AGENT_RESOURCE_NAME}" | grep -oP 'reasoningEngines/\K[0-9]+')
AGENT_URL="https://${REGION}-aiplatform.googleapis.com/v1beta1/${AGENT_RESOURCE_NAME}"

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
