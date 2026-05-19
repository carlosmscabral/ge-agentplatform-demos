#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a; source "${SCRIPT_DIR}/.env"; set +a
fi

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
STAGING_BUCKET="${STAGING_BUCKET:-${PROJECT_ID}-oauth-3lo-staging}"
AUTH_PROVIDER_NAME="${AUTH_PROVIDER_NAME:-oauth-3lo-keycloak}"
AUTH_PROVIDER_LOCATION="${AUTH_PROVIDER_LOCATION:-us-central1}"
MCP_SERVICE_NAME="${MCP_SERVICE_NAME:-oauth-3lo-mcp}"
MCP_REGISTRY_DISPLAY_NAME="${MCP_REGISTRY_DISPLAY_NAME:-oauth-3lo-mcp}"
FRONTEND_SERVICE_NAME="${FRONTEND_SERVICE_NAME:-oauth-3lo-frontend}"

METADATA_FILE="${SCRIPT_DIR}/deployment_metadata.json"
AGENT_METADATA="${SCRIPT_DIR}/agent/deployment_metadata.json"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Agent Platform OAuth 3LO + Keycloak Demo — Teardown       ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project: ${PROJECT_ID}"
echo "  Region:  ${REGION}"
echo ""

# ─── Optional: Deregister from Gemini Enterprise ────────────────────────────
if [ -n "${GEMINI_ENTERPRISE_APP_ID:-}" ] && [ -n "${GE_AGENT_ID:-}" ]; then
    echo ">>> Deregistering from Gemini Enterprise…"
    ACCESS_TOKEN=$(gcloud auth print-access-token)
    curl -s -X DELETE \
        "https://global-discoveryengine.googleapis.com/v1alpha/${GEMINI_ENTERPRISE_APP_ID}/assistants/default_assistant/agents/${GE_AGENT_ID}" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" || echo "    GE agent not found."
    echo ""
fi

# ─── Step 0: Delete the Agent Registry Binding (before deleting agent/MCP) ───
BINDING_NAME="${BINDING_NAME:-${AGENT_DISPLAY_NAME:-oauth-3lo-agent}-binding}"
echo ">>> Step 0: Deleting Binding ${BINDING_NAME}…"
gcloud alpha agent-registry bindings delete "${BINDING_NAME}" \
    --location="${REGION}" --project="${PROJECT_ID}" --quiet 2>/dev/null \
    || echo "    Binding not found."

# ─── Step 1: Delete frontend Cloud Run service ──────────────────────────────
echo ""
echo ">>> Step 1/6: Deleting frontend Cloud Run service…"
gcloud run services delete "${FRONTEND_SERVICE_NAME}" \
    --region="${REGION}" --project="${PROJECT_ID}" --quiet 2>/dev/null \
    || echo "    Frontend not found."

# ─── Step 2: Delete the ADK agent ───────────────────────────────────────────
echo ""
echo ">>> Step 2/6: Deleting ADK agent…"
if [ -f "${AGENT_METADATA}" ]; then
    AGENT_RESOURCE=$(python3 -c "import json; print(json.load(open('${AGENT_METADATA}'))['remote_agent_runtime_id'])")
    RE_ID=$(echo "${AGENT_RESOURCE}" | grep -oP 'reasoningEngines/\K[0-9]+')
    PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
    ACCESS_TOKEN=$(gcloud auth print-access-token)
    curl -s -X DELETE \
        "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines/${RE_ID}?force=true" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" > /dev/null || echo "    Agent not found."
    command rm -f "${AGENT_METADATA}"
    echo "    Deleted agent: ${AGENT_RESOURCE}"
else
    echo "    No agent deployment artifact found, skipping."
fi

# ─── Step 3: Delete MCP server from Agent Registry ──────────────────────────
echo ""
echo ">>> Step 3/6: Deleting MCP server from Agent Registry…"
# Look up by the service NAME (which equals MCP_REGISTRY_DISPLAY_NAME at create
# time) rather than the mirror's displayName field. The mirror's displayName
# can drift across re-deploys if a prior deploy used a different value, so
# filtering by displayName misses the orphan and leaves it behind. The service
# name in agent-registry services/ is the stable identifier.
if gcloud alpha agent-registry services describe "${MCP_REGISTRY_DISPLAY_NAME}" \
        --location="${REGION}" --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
    gcloud alpha agent-registry services delete "${MCP_REGISTRY_DISPLAY_NAME}" \
        --location="${REGION}" --project="${PROJECT_ID}" --quiet 2>/dev/null \
        && echo "    Removed agent-registry service ${MCP_REGISTRY_DISPLAY_NAME}" \
        || echo "    Registry service delete failed (may not be supported)."
else
    echo "    No MCP registry entry found, skipping."
fi

# ─── Step 4: Delete MCP Cloud Run service ───────────────────────────────────
echo ""
echo ">>> Step 4/6: Deleting MCP Cloud Run service…"
gcloud run services delete "${MCP_SERVICE_NAME}" \
    --region="${REGION}" --project="${PROJECT_ID}" --quiet 2>/dev/null \
    || echo "    MCP service not found."

# ─── Step 5: Delete Agent Identity auth provider ────────────────────────────
echo ""
echo ">>> Step 5/6: Deleting Agent Identity auth provider…"
gcloud alpha agent-identity connectors delete "${AUTH_PROVIDER_NAME}" \
    --location="${AUTH_PROVIDER_LOCATION}" \
    --project="${PROJECT_ID}" --quiet 2>/dev/null \
    || echo "    Auth provider not found."

# ─── Step 6: Delete staging bucket ──────────────────────────────────────────
echo ""
echo ">>> Step 6/6: Deleting staging bucket…"
gcloud storage rm --recursive "gs://${STAGING_BUCKET}" --quiet 2>/dev/null \
    || echo "    Bucket not found or already deleted."

command rm -f "${METADATA_FILE}"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                   Teardown Complete                         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
