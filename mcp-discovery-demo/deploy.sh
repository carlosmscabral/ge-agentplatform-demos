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
REGISTRY_LOCATION="${REGISTRY_LOCATION:-us-central1}"
STAGING_BUCKET="${STAGING_BUCKET:-${PROJECT_ID}-mcp-discovery-staging}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3-flash-preview}"

MARKET_MCP_SERVICE="${MARKET_MCP_SERVICE:-fintoolkit-market-data-mcp}"
PORTFOLIO_MCP_SERVICE="${PORTFOLIO_MCP_SERVICE:-fintoolkit-portfolio-mcp}"
NEWS_MCP_SERVICE="${NEWS_MCP_SERVICE:-fintoolkit-news-sentiment-mcp}"

MARKET_MCP_DISPLAY="${MARKET_MCP_DISPLAY:-market-data}"
PORTFOLIO_MCP_DISPLAY="${PORTFOLIO_MCP_DISPLAY:-portfolio}"
NEWS_MCP_DISPLAY="${NEWS_MCP_DISPLAY:-news-sentiment}"

ORCHESTRATOR_DISPLAY_NAME="${ORCHESTRATOR_DISPLAY_NAME:-fintoolkit-orchestrator}"

export PROJECT_ID REGION

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   mcp-discovery-demo — Financial Toolkit Deploy             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:    ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "  Region:     ${REGION}"
echo "  Model:      ${GEMINI_MODEL}"
echo "  MCPs:       ${MARKET_MCP_SERVICE}, ${PORTFOLIO_MCP_SERVICE}, ${NEWS_MCP_SERVICE}"
echo "  Agent:      ${ORCHESTRATOR_DISPLAY_NAME} (SPIFFE identity)"
echo ""

# ─── Step 1: Staging bucket ──────────────────────────────────────────────────
echo ">>> Step 1/9: Creating staging bucket gs://${STAGING_BUCKET}..."
gcloud storage buckets create "gs://${STAGING_BUCKET}" \
    --location="${REGION}" --uniform-bucket-level-access --quiet 2>/dev/null \
    || echo "    Bucket already exists."

# ─── Step 2: Baseline IAM via SPIFFE principal set ───────────────────────────
echo ""
echo ">>> Step 2/9: Granting baseline IAM roles to SPIFFE principal set..."

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
    "roles/agentregistry.viewer"
)

for ROLE in "${BASELINE_ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="${PRINCIPAL_SET}" --role="${ROLE}" \
        --condition=None --quiet > /dev/null 2>&1 || true
    echo "    ✓ ${ROLE}"
done

# ─── Step 3: Build & deploy 3 Cloud Run MCP services ─────────────────────────
echo ""
echo ">>> Step 3/9: Building & deploying 3 MCP servers to Cloud Run..."

declare -A MCP_URL
declare -A MCP_TAG
MCP_TAG["${MARKET_MCP_SERVICE}"]="market"
MCP_TAG["${PORTFOLIO_MCP_SERVICE}"]="portfolio"
MCP_TAG["${NEWS_MCP_SERVICE}"]="news"

declare -A MCP_DIR
MCP_DIR["${MARKET_MCP_SERVICE}"]="market-data-mcp"
MCP_DIR["${PORTFOLIO_MCP_SERVICE}"]="portfolio-mcp"
MCP_DIR["${NEWS_MCP_SERVICE}"]="news-sentiment-mcp"

declare -A MCP_DISPLAY
MCP_DISPLAY["${MARKET_MCP_SERVICE}"]="${MARKET_MCP_DISPLAY}"
MCP_DISPLAY["${PORTFOLIO_MCP_SERVICE}"]="${PORTFOLIO_MCP_DISPLAY}"
MCP_DISPLAY["${NEWS_MCP_SERVICE}"]="${NEWS_MCP_DISPLAY}"

PARALLEL_LOG_DIR="$(mktemp -d)"
PARALLEL_PIDS=()
for SVC in "${MARKET_MCP_SERVICE}" "${PORTFOLIO_MCP_SERVICE}" "${NEWS_MCP_SERVICE}"; do
    DIR="${MCP_DIR[${SVC}]}"
    LOG="${PARALLEL_LOG_DIR}/${SVC}.log"
    echo "  → Launching ${SVC} (log: ${LOG})..."
    # NOTE: Cloud Run does not support SPIFFE identity (only Agent Runtime and
    # Gemini Enterprise do, as of 2026-05). Cloud Run's IAM enforcer only accepts
    # OIDC ID tokens, not the SPIFFE-bound access tokens the agent has — and the
    # agent has no GCE metadata server to mint ID tokens from. The pragmatic
    # compromise for this demo: `--allow-unauthenticated`. Production hardening
    # would use Agent Gateway in front of these services (default-deny IAP
    # policies on tool granularity), or migrate the MCPs to GKE with Managed
    # Workload Identity to get SPIFFE end-to-end. See ARCHITECTURE.md §2c.
    (
        gcloud run deploy "${SVC}" \
            --source="${SCRIPT_DIR}/${DIR}" \
            --region="${REGION}" \
            --allow-unauthenticated \
            --port=8080 \
            --memory=512Mi \
            --cpu=1 \
            --max-instances=3 \
            --quiet > "${LOG}" 2>&1
    ) &
    PARALLEL_PIDS+=($!)
done

echo "  Waiting for 3 parallel Cloud Run builds + deploys..."
FAILED=0
for PID in "${PARALLEL_PIDS[@]}"; do
    if ! wait "${PID}"; then
        FAILED=1
    fi
done
if [ "${FAILED}" -ne 0 ]; then
    echo "  ✗ One or more Cloud Run deploys failed. Logs at ${PARALLEL_LOG_DIR}/"
    for SVC in "${MARKET_MCP_SERVICE}" "${PORTFOLIO_MCP_SERVICE}" "${NEWS_MCP_SERVICE}"; do
        echo "  ── ${SVC} ──"
        tail -20 "${PARALLEL_LOG_DIR}/${SVC}.log" || true
    done
    exit 1
fi

for SVC in "${MARKET_MCP_SERVICE}" "${PORTFOLIO_MCP_SERVICE}" "${NEWS_MCP_SERVICE}"; do
    MCP_URL["${SVC}"]=$(gcloud run services describe "${SVC}" \
        --region="${REGION}" --format='value(status.url)')
    echo "    ✓ ${SVC} → ${MCP_URL[${SVC}]}"
done
rm -rf "${PARALLEL_LOG_DIR}"

# ─── Step 4: Register each MCP in Agent Registry ─────────────────────────────
echo ""
echo ">>> Step 4/9: Registering MCP servers in Agent Registry..."

declare -A MCP_REGISTRY_NAME

for SVC in "${MARKET_MCP_SERVICE}" "${PORTFOLIO_MCP_SERVICE}" "${NEWS_MCP_SERVICE}"; do
    DIR="${MCP_DIR[${SVC}]}"
    DISPLAY="${MCP_DISPLAY[${SVC}]}"
    TAG="${MCP_TAG[${SVC}]}"
    URL="${MCP_URL[${SVC}]}/mcp"
    SPEC_PATH="${SCRIPT_DIR}/${DIR}/toolspec.json"

    # Idempotency: delete then create. Service is also deleted to avoid URL-uniqueness errors.
    gcloud alpha agent-registry services delete "${SVC}" \
        --location="${REGISTRY_LOCATION}" --quiet 2>/dev/null || true

    SPEC_CONTENT=$(cat "${SPEC_PATH}")
    # NOTE: `gcloud alpha agent-registry services create` does not expose --attributes
    # or --labels (as of 2026-05). We encode the category tag in the description as
    # `[tag:X]` so discover_tools_by_category can parse it (see app/discovery.py).
    echo "  → Registering ${SVC} (tag=${TAG}, display=${DISPLAY}, url=${URL})..."
    gcloud alpha agent-registry services create "${SVC}" \
        --location="${REGISTRY_LOCATION}" \
        --display-name="${DISPLAY}" \
        --description="[tag:${TAG}] [domain:finance] ${DISPLAY} MCP server for the fintoolkit demo." \
        --interfaces="protocolBinding=jsonrpc,url=${URL}" \
        --mcp-server-spec-type=tool-spec \
        --mcp-server-spec-content="${SPEC_CONTENT}" \
        --quiet

    # Capture the mcpServer resource name (different from service name — uses agentregistry-UUID)
    sleep 3
    MCP_REGISTRY_NAME["${SVC}"]=$(gcloud alpha agent-registry mcp-servers list \
        --location="${REGISTRY_LOCATION}" \
        --filter="displayName=${DISPLAY}" \
        --format='value(name)' 2>/dev/null | head -1 || echo "")
    if [ -n "${MCP_REGISTRY_NAME[${SVC}]}" ]; then
        echo "    ✓ mcpServer: ${MCP_REGISTRY_NAME[${SVC}]}"
    else
        echo "    ⚠ mcpServer name not yet visible (registry sync in progress)"
    fi
done

# ─── Step 5: uv lock orchestrator (Rule #6) ──────────────────────────────────
echo ""
echo ">>> Step 5/9: Refreshing uv.lock for orchestrator..."
cd "${SCRIPT_DIR}/orchestrator-agent"
uv lock --quiet 2>/dev/null || true

# ─── Step 6: Deploy orchestrator with SPIFFE identity ────────────────────────
echo ""
echo ">>> Step 6/9: Deploying orchestrator (SPIFFE identity + MCP URLs in env)..."

ORCH_ENV_VARS="GEMINI_MODEL=${GEMINI_MODEL}"
ORCH_ENV_VARS="${ORCH_ENV_VARS},GOOGLE_CLOUD_LOCATION=global"
ORCH_ENV_VARS="${ORCH_ENV_VARS},GOOGLE_CLOUD_REGION=${REGION}"
ORCH_ENV_VARS="${ORCH_ENV_VARS},REGISTRY_LOCATION=${REGISTRY_LOCATION}"
ORCH_ENV_VARS="${ORCH_ENV_VARS},LOGS_BUCKET_NAME=${STAGING_BUCKET}"
ORCH_ENV_VARS="${ORCH_ENV_VARS},OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY"
ORCH_ENV_VARS="${ORCH_ENV_VARS},OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental"
ORCH_ENV_VARS="${ORCH_ENV_VARS},ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false"
ORCH_ENV_VARS="${ORCH_ENV_VARS},GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False"
# Option B: agent has ZERO knowledge of specific MCPs at deploy time. It only
# needs Registry access (granted via SPIFFE principalSet + agentregistry.viewer).
# Discovery + dynamic invocation happens entirely at runtime via the Registry.
# See ARCHITECTURE.md §3.

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

# ─── Step 7: Extract orchestrator SPIFFE identity ────────────────────────────
echo ""
echo ">>> Step 7/9: Extracting orchestrator SPIFFE identity..."

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
    echo "  ⚠ SPIFFE ID not yet visible — IAM grants for Cloud Run will be skipped."
    echo "    Re-run deploy.sh once provisioning completes (~1 min)."
else
    echo "  ✓ Orchestrator SPIFFE: ${ORCH_SPIFFE}"
    echo "${ORCH_SPIFFE}" > "${SCRIPT_DIR}/.deploy-state"
fi

cd "${SCRIPT_DIR}"

# ─── Step 8: Cloud Run access model ──────────────────────────────────────────
echo ""
echo ">>> Step 8/9: Cloud Run access model — MCPs are --allow-unauthenticated"
echo "    Rationale: Cloud Run IAM accepts OIDC ID tokens only; Agent Runtime"
echo "    does not expose a GCE metadata server to mint them from a SPIFFE token."
echo "    Production hardening: Agent Gateway in front, or migrate MCPs to GKE"
echo "    with Managed Workload Identity (see ARCHITECTURE.md §2c)."

# ─── Step 9: Summary ─────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Deployment Complete                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Orchestrator: ${ORCHESTRATOR_RESOURCE}"
echo "║  SPIFFE:       ${ORCH_SPIFFE:-'(pending)'}"
echo "║"
echo "║  MCP servers (Cloud Run):"
for SVC in "${MARKET_MCP_SERVICE}" "${PORTFOLIO_MCP_SERVICE}" "${NEWS_MCP_SERVICE}"; do
    echo "║    ${SVC} → ${MCP_URL[${SVC}]}"
done
echo "║"
echo "║  Agent Registry MCP entries:"
for SVC in "${MARKET_MCP_SERVICE}" "${PORTFOLIO_MCP_SERVICE}" "${NEWS_MCP_SERVICE}"; do
    echo "║    ${MCP_DISPLAY[${SVC}]} → ${MCP_REGISTRY_NAME[${SVC}]:-'(syncing)'}"
done
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Try the orchestrator:"
echo "  cd orchestrator-agent && agents-cli run --url '${ORCHESTRATOR_URL}' --mode adk 'Mostre meu portfolio account-001'"
echo ""
echo "Inspect Agent Registry:"
echo "  gcloud alpha agent-registry mcp-servers list --location=${REGISTRY_LOCATION}"
echo ""
echo "View traces:"
echo "  https://console.cloud.google.com/traces/list?project=${PROJECT_ID}"
