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
STAGING_BUCKET="${STAGING_BUCKET:-${PROJECT_ID}-code-exec-staging}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"
ORCHESTRATOR_DISPLAY_NAME="${ORCHESTRATOR_DISPLAY_NAME:-code-analyst}"

export PROJECT_ID REGION

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   code-execution-demo — Code Analyst Deploy                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:        ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "  Region:         ${REGION}"
echo "  Model:          ${GEMINI_MODEL}"
echo "  Orchestrator:   ${ORCHESTRATOR_DISPLAY_NAME} (SPIFFE identity)"
echo "  Code executor:  Gemini built-in (gVisor sandbox via Gemini API)"
echo ""

# ─── Step 1: Staging bucket ──────────────────────────────────────────────────
echo ">>> Step 1/6: Creating staging bucket gs://${STAGING_BUCKET}..."
gcloud storage buckets create "gs://${STAGING_BUCKET}" \
    --location="${REGION}" --uniform-bucket-level-access --quiet 2>/dev/null \
    || echo "    Bucket already exists."

# ─── Step 2: Baseline IAM via SPIFFE principal set ───────────────────────────
echo ""
echo ">>> Step 2/6: Granting baseline IAM roles to SPIFFE principal set..."

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
        --member="${PRINCIPAL_SET}" --role="${ROLE}" \
        --condition=None --quiet > /dev/null 2>&1 || true
    echo "    ✓ ${ROLE}"
done

# ─── Step 3: uv lock orchestrator (Rule #6) ──────────────────────────────────
echo ""
echo ">>> Step 3/6: Refreshing uv.lock for analyst-agent..."
cd "${SCRIPT_DIR}/analyst-agent"
uv lock --quiet 2>/dev/null || true

# ─── Step 4: Deploy orchestrator with SPIFFE identity ────────────────────────
echo ""
echo ">>> Step 4/6: Deploying orchestrator (SPIFFE identity)..."

ORCH_ENV_VARS="GEMINI_MODEL=${GEMINI_MODEL}"
ORCH_ENV_VARS="${ORCH_ENV_VARS},GOOGLE_CLOUD_LOCATION=global"
ORCH_ENV_VARS="${ORCH_ENV_VARS},GOOGLE_CLOUD_REGION=${REGION}"
ORCH_ENV_VARS="${ORCH_ENV_VARS},LOGS_BUCKET_NAME=${STAGING_BUCKET}"
ORCH_ENV_VARS="${ORCH_ENV_VARS},OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY"
ORCH_ENV_VARS="${ORCH_ENV_VARS},OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental"
ORCH_ENV_VARS="${ORCH_ENV_VARS},ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false"
ORCH_ENV_VARS="${ORCH_ENV_VARS},GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False"

agents-cli deploy \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --agent-identity \
    --update-env-vars "${ORCH_ENV_VARS}" \
    --no-confirm-project

ORCHESTRATOR_RESOURCE=$(python3 -c "import json; print(json.load(open('deployment_metadata.json'))['remote_agent_runtime_id'])")
ORCHESTRATOR_RE_ID=$(echo "${ORCHESTRATOR_RESOURCE}" | grep -oP 'reasoningEngines/\K[0-9]+')
ORCHESTRATOR_URL="https://${REGION}-aiplatform.googleapis.com/v1beta1/${ORCHESTRATOR_RESOURCE}"

echo ""
echo "  Orchestrator: ${ORCHESTRATOR_RESOURCE}"
echo "  URL:          ${ORCHESTRATOR_URL}"

# ─── Step 5: Extract orchestrator SPIFFE identity ────────────────────────────
echo ""
echo ">>> Step 5/6: Extracting orchestrator SPIFFE identity..."
ACCESS_TOKEN=$(gcloud auth print-access-token)
ORCH_SPIFFE=""
for i in 1 2 3 4 5; do
    ORCH_SPIFFE=$(curl -s \
        "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines/${ORCHESTRATOR_RE_ID}" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('spec',{}).get('effectiveIdentity',''))" 2>/dev/null || echo "")
    if [ -n "${ORCH_SPIFFE}" ]; then break; fi
    echo "    Waiting for SPIFFE provisioning (attempt ${i}/5)..."
    sleep 10
done

if [ -z "${ORCH_SPIFFE}" ]; then
    echo "  ⚠ SPIFFE ID not yet visible."
else
    echo "  ✓ Orchestrator SPIFFE: ${ORCH_SPIFFE}"
fi

cd "${SCRIPT_DIR}"

# Persist deploy state for undeploy
cat > "${SCRIPT_DIR}/.deploy-state" <<EOF
ORCH_RESOURCE=${ORCHESTRATOR_RESOURCE}
ORCH_SPIFFE=${ORCH_SPIFFE}
EOF

# ─── Step 6: Summary ─────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Deployment Complete                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Orchestrator:  ${ORCHESTRATOR_RESOURCE}"
echo "║  SPIFFE:        ${ORCH_SPIFFE:-'(pending)'}"
echo "║  Code exec:     Gemini built-in (gVisor)"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Test the agent:"
echo "  cd analyst-agent && agents-cli run --url '${ORCHESTRATOR_URL}' --mode adk \\"
echo "    'Crie um DataFrame com 1000 vendas sintéticas (seed=42) e mostre .describe()'"
echo ""
echo "View traces:"
echo "  https://console.cloud.google.com/traces/list?project=${PROJECT_ID}"
