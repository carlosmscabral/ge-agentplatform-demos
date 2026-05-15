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
STAGING_BUCKET="${STAGING_BUCKET:-${PROJECT_ID}-spiffe-registry-staging}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3-flash-preview}"
SPECIALIST_DISPLAY_NAME="${SPECIALIST_DISPLAY_NAME:-spiffe-specialist}"
ORCHESTRATOR_DISPLAY_NAME="${ORCHESTRATOR_DISPLAY_NAME:-spiffe-orchestrator}"
REGISTRY_LOCATION="${REGISTRY_LOCATION:-us-central1}"

export PROJECT_ID REGION

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║     Agent Platform SPIFFE + Registry Demo — Deploy          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:      ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "  Region:       ${REGION}"
echo "  Model:        ${GEMINI_MODEL}"
echo "  Specialist:   ${SPECIALIST_DISPLAY_NAME}"
echo "  Orchestrator: ${ORCHESTRATOR_DISPLAY_NAME}"
echo "  Identity:     SPIFFE (--agent-identity)"
echo ""

# ─── Step 1: Create Staging GCS Bucket ───────────────────────────────────────
echo ">>> Step 1/6: Creating staging bucket gs://${STAGING_BUCKET}..."
gcloud storage buckets create "gs://${STAGING_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --quiet 2>/dev/null || echo "    Bucket already exists."

# ─── Step 2: Grant baseline IAM roles via principal set ─────────────────────
echo ""
echo ">>> Step 2/7: Granting baseline IAM roles to all SPIFFE agents..."

ORG_ID=$(gcloud organizations list --format='value(ID)' --limit=1 2>/dev/null || echo "")
if [ -z "${ORG_ID}" ]; then
    PRINCIPAL_SET="principalSet://agents.global.project-${PROJECT_NUMBER}.system.id.goog/attribute.platformContainer/aiplatform/projects/${PROJECT_NUMBER}"
else
    PRINCIPAL_SET="principalSet://agents.global.org-${ORG_ID}.system.id.goog/attribute.platformContainer/aiplatform/projects/${PROJECT_NUMBER}"
fi
echo "  Principal set: ${PRINCIPAL_SET}"

BASELINE_ROLES=(
    "roles/aiplatform.agentDefaultAccess"
    "roles/aiplatform.user"
    "roles/serviceusage.serviceUsageConsumer"
    "roles/logging.logWriter"
    "roles/monitoring.metricWriter"
    "roles/cloudapiregistry.viewer"
    "roles/storage.objectAdmin"
)

for ROLE in "${BASELINE_ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="${PRINCIPAL_SET}" \
        --role="${ROLE}" \
        --condition=None --quiet > /dev/null 2>&1 || true
    echo "    ✓ ${ROLE}"
done

# ─── Step 3: Deploy Specialist Agent with SPIFFE Identity ────────────────────
echo ""
echo ">>> Step 3/7: Deploying Specialist Agent (A2A) with SPIFFE identity..."
cd "${SCRIPT_DIR}/specialist-agent"
uv lock --quiet 2>/dev/null || true

agents-cli deploy \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --agent-identity \
    --update-env-vars "GEMINI_MODEL=${GEMINI_MODEL},GOOGLE_CLOUD_LOCATION=global,GOOGLE_CLOUD_REGION=${REGION},LOGS_BUCKET_NAME=${STAGING_BUCKET},OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY,OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false,GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False" \
    --no-confirm-project

SPECIALIST_RESOURCE=$(python3 -c "import json; print(json.load(open('deployment_metadata.json'))['remote_agent_runtime_id'])")
SPECIALIST_RE_ID=$(echo "${SPECIALIST_RESOURCE}" | grep -oP 'reasoningEngines/\K[0-9]+')
SPECIALIST_URL="https://${REGION}-aiplatform.googleapis.com/v1beta1/${SPECIALIST_RESOURCE}"

echo ""
echo "  Specialist deployed: ${SPECIALIST_RESOURCE}"
echo "  Specialist URL:      ${SPECIALIST_URL}"

# Extract SPIFFE ID
echo "  Extracting SPIFFE identity..."
ACCESS_TOKEN=$(gcloud auth print-access-token)
SPECIALIST_SPIFFE=""
for i in 1 2 3 4 5; do
    SPECIALIST_SPIFFE=$(curl -s \
        "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines/${SPECIALIST_RE_ID}" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('spec',{}).get('effectiveIdentity',''))" 2>/dev/null || echo "")
    if [ -n "${SPECIALIST_SPIFFE}" ]; then
        break
    fi
    echo "    Waiting for SPIFFE identity to be provisioned (attempt ${i}/5)..."
    sleep 10
done

if [ -n "${SPECIALIST_SPIFFE}" ]; then
    echo "  ✓ Specialist SPIFFE: ${SPECIALIST_SPIFFE}"
else
    echo "  ⚠ Specialist SPIFFE ID not yet available (may take a few minutes)"
fi

cd "${SCRIPT_DIR}"

# ─── Step 3: Verify Specialist in Agent Registry ────────────────────────────
echo ""
echo ">>> Step 4/7: Verifying Specialist in Agent Registry..."
echo "  Waiting 15s for registry sync..."
sleep 15

REGISTRY_ENTRY=$(gcloud alpha agent-registry agents list \
    --location="${REGISTRY_LOCATION}" \
    --project="${PROJECT_ID}" \
    --filter="displayName='spiffe_currency_specialist' OR displayName='${SPECIALIST_DISPLAY_NAME}'" \
    --format="yaml" 2>/dev/null || echo "")

if [ -n "${REGISTRY_ENTRY}" ]; then
    echo "  ✓ Specialist found in Agent Registry!"

    SPECIALIST_REGISTRY_NAME=$(echo "${REGISTRY_ENTRY}" | grep "^name:" | head -1 | awk '{print $2}')
    REGISTRY_IDENTITY=$(echo "${REGISTRY_ENTRY}" | grep -A1 "RuntimeIdentity:" | grep "principal:" | awk '{print $2}' || echo "(not found)")

    echo "  Registry resource: ${SPECIALIST_REGISTRY_NAME}"
    echo "  Registry identity: ${REGISTRY_IDENTITY}"

    REGISTRY_PROTOCOL=$(echo "${REGISTRY_ENTRY}" | grep "type: A2A_AGENT" || echo "")
    if [ -n "${REGISTRY_PROTOCOL}" ]; then
        echo "  ✓ Protocol: A2A_AGENT (agent card extracted)"
    else
        echo "  ⚠ Protocol: CUSTOM (A2A card not extracted)"
    fi
else
    echo "  ⚠ Specialist not found in Agent Registry yet"
    echo "    This may take a few minutes. Proceeding with URL fallback."
    SPECIALIST_REGISTRY_NAME=""
fi

# A2A card URL for fallback
SPECIALIST_A2A_CARD_URL="${SPECIALIST_URL}/a2a/v1/card"
echo ""
echo "  A2A card URL (fallback): ${SPECIALIST_A2A_CARD_URL}"

# ─── Step 4: Deploy Orchestrator Agent with SPIFFE Identity ──────────────────
echo ""
echo ">>> Step 5/7: Deploying Orchestrator Agent with SPIFFE identity + registry discovery..."
cd "${SCRIPT_DIR}/orchestrator-agent"
uv lock --quiet 2>/dev/null || true

ORCHESTRATOR_ENV_VARS="GEMINI_MODEL=${GEMINI_MODEL},GOOGLE_CLOUD_LOCATION=global,GOOGLE_CLOUD_REGION=${REGION},REGISTRY_LOCATION=${REGISTRY_LOCATION},LOGS_BUCKET_NAME=${STAGING_BUCKET},OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY,OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false,GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False"

if [ -n "${SPECIALIST_REGISTRY_NAME}" ]; then
    ORCHESTRATOR_ENV_VARS="${ORCHESTRATOR_ENV_VARS},SPECIALIST_REGISTRY_NAME=${SPECIALIST_REGISTRY_NAME}"
    echo "  Discovery: Agent Registry (${SPECIALIST_REGISTRY_NAME})"
fi

ORCHESTRATOR_ENV_VARS="${ORCHESTRATOR_ENV_VARS},SPECIALIST_A2A_CARD_URL=${SPECIALIST_A2A_CARD_URL}"
echo "  Fallback:  A2A card URL (${SPECIALIST_A2A_CARD_URL})"

agents-cli deploy \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --agent-identity \
    --update-env-vars "${ORCHESTRATOR_ENV_VARS}" \
    --no-confirm-project

ORCHESTRATOR_RESOURCE=$(python3 -c "import json; print(json.load(open('deployment_metadata.json'))['remote_agent_runtime_id'])")
ORCHESTRATOR_RE_ID=$(echo "${ORCHESTRATOR_RESOURCE}" | grep -oP 'reasoningEngines/\K[0-9]+')
ORCHESTRATOR_URL="https://${REGION}-aiplatform.googleapis.com/v1beta1/${ORCHESTRATOR_RESOURCE}"

echo ""
echo "  Orchestrator deployed: ${ORCHESTRATOR_RESOURCE}"
echo "  Orchestrator URL:      ${ORCHESTRATOR_URL}"

# Extract SPIFFE ID
echo "  Extracting SPIFFE identity..."
ACCESS_TOKEN=$(gcloud auth print-access-token)
ORCHESTRATOR_SPIFFE=""
for i in 1 2 3; do
    ORCHESTRATOR_SPIFFE=$(curl -s \
        "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines/${ORCHESTRATOR_RE_ID}" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('spec',{}).get('effectiveIdentity',''))" 2>/dev/null || echo "")
    if [ -n "${ORCHESTRATOR_SPIFFE}" ]; then
        break
    fi
    echo "    Waiting for SPIFFE identity (attempt ${i}/3)..."
    sleep 10
done

if [ -n "${ORCHESTRATOR_SPIFFE}" ]; then
    echo "  ✓ Orchestrator SPIFFE: ${ORCHESTRATOR_SPIFFE}"
else
    echo "  ⚠ Orchestrator SPIFFE ID not yet available"
fi

cd "${SCRIPT_DIR}"

# ─── Step 5: Verify Both Agents in Agent Registry ───────────────────────────
echo ""
echo ">>> Step 6/7: Verifying both agents in Agent Registry..."
echo ""
echo "  All agents in registry:"
gcloud alpha agent-registry agents list \
    --location="${REGISTRY_LOCATION}" \
    --project="${PROJECT_ID}" \
    --format="table(displayName,name.segment(5):label=REGISTRY_ID,protocols[0].type:label=PROTOCOL,attributes.flatten())" \
    --filter="agentId:'urn:agent:projects-${PROJECT_NUMBER}:projects:${PROJECT_NUMBER}:locations:${REGION}:aiplatform:reasoningEngines'" \
    2>/dev/null || echo "  (Could not list agents)"

# ─── Step 6: Optional GE Registration ───────────────────────────────────────
if [ -n "${GEMINI_ENTERPRISE_APP_ID:-}" ]; then
    echo ""
    echo ">>> Step 7/7: Registering with Gemini Enterprise..."
    cd "${SCRIPT_DIR}/orchestrator-agent"
    agents-cli publish gemini-enterprise \
        --gemini-enterprise-app-id "${GEMINI_ENTERPRISE_APP_ID}" \
        --display-name "${GEMINI_DISPLAY_NAME:-${ORCHESTRATOR_DISPLAY_NAME}}" \
        --description "${GEMINI_DESCRIPTION:-SPIFFE + Registry orchestrator demo}" \
        --no-confirm-project \
        || echo "    GE registration failed (non-blocking)."
    cd "${SCRIPT_DIR}"
else
    echo ""
    echo ">>> Step 7/7: Skipping Gemini Enterprise registration (GEMINI_ENTERPRISE_APP_ID not set)."
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Deployment Complete                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Specialist:   ${SPECIALIST_RESOURCE}"
echo "║  Orchestrator: ${ORCHESTRATOR_RESOURCE}"
echo "║  Identity:     SPIFFE"
echo "║  Specialist SPIFFE:   ${SPECIALIST_SPIFFE:-'(pending)'}"
echo "║  Orchestrator SPIFFE: ${ORCHESTRATOR_SPIFFE:-'(pending)'}"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Test specialist (A2A mode):"
echo "  cd specialist-agent && agents-cli run --url '${SPECIALIST_URL}' --mode a2a 'Converta 100 USD para BRL'"
echo ""
echo "Test orchestrator (ADK mode — discovery via registry):"
echo "  cd orchestrator-agent && agents-cli run --url '${ORCHESTRATOR_URL}' --mode adk 'Qual a cotação do dólar hoje?'"
echo ""
echo "Inspect registry:"
echo "  gcloud alpha agent-registry agents list --location=${REGISTRY_LOCATION} --project=${PROJECT_ID}"
