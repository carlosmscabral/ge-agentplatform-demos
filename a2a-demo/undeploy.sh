#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Load Configuration ─────────────────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a; source "${SCRIPT_DIR}/.env"; set +a
fi

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          Agent Platform A2A Demo — Teardown                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:  ${PROJECT_ID}"
echo "  Region:   ${REGION}"
echo ""

# ─── Optional: Deregister from Gemini Enterprise ────────────────────────────
if [ -n "${GEMINI_ENTERPRISE_APP_ID:-}" ] && [ -n "${GE_AGENT_ID:-}" ]; then
    echo ">>> Deregistering from Gemini Enterprise..."
    ACCESS_TOKEN=$(gcloud auth print-access-token)
    curl -s -X DELETE \
        "https://global-discoveryengine.googleapis.com/v1alpha/${GEMINI_ENTERPRISE_APP_ID}/assistants/default_assistant/agents/${GE_AGENT_ID}" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" || echo "    GE agent not found."
    echo ""
fi

# ─── Step 1: Delete Orchestrator Agent (depends on specialist) ──────────────
echo ">>> Step 1/3: Deleting Orchestrator Agent..."
if [ -f "${SCRIPT_DIR}/orchestrator-agent/deployment_metadata.json" ]; then
    AGENT_RESOURCE_NAME=$(python3 -c "import json; print(json.load(open('${SCRIPT_DIR}/orchestrator-agent/deployment_metadata.json'))['remote_agent_runtime_id'])")
    RE_ID=$(echo "${AGENT_RESOURCE_NAME}" | grep -oP 'reasoningEngines/\K[0-9]+')
    PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
    ACCESS_TOKEN=$(gcloud auth print-access-token)
    curl -s -X DELETE \
        "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines/${RE_ID}?force=true" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" || echo "    Agent not found."
    command rm -f "${SCRIPT_DIR}/orchestrator-agent/deployment_metadata.json"
else
    echo "    No orchestrator deployment artifact found, skipping."
fi

# ─── Step 2: Delete Specialist Agent ────────────────────────────────────────
echo ""
echo ">>> Step 2/3: Deleting Specialist Agent..."
if [ -f "${SCRIPT_DIR}/specialist-agent/deployment_metadata.json" ]; then
    AGENT_RESOURCE_NAME=$(python3 -c "import json; print(json.load(open('${SCRIPT_DIR}/specialist-agent/deployment_metadata.json'))['remote_agent_runtime_id'])")
    RE_ID=$(echo "${AGENT_RESOURCE_NAME}" | grep -oP 'reasoningEngines/\K[0-9]+')
    PROJECT_NUMBER="${PROJECT_NUMBER:-$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')}"
    ACCESS_TOKEN="${ACCESS_TOKEN:-$(gcloud auth print-access-token)}"
    curl -s -X DELETE \
        "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines/${RE_ID}?force=true" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" || echo "    Agent not found."
    command rm -f "${SCRIPT_DIR}/specialist-agent/deployment_metadata.json"
else
    echo "    No specialist deployment artifact found, skipping."
fi

# ─── Step 3: Staging Bucket Cleanup ─────────────────────────────────────────
STAGING_BUCKET="${STAGING_BUCKET:-${PROJECT_ID}-a2a-demo-staging}"
echo ""
echo ">>> Step 3/3: Staging bucket cleanup..."
gcloud storage rm --recursive "gs://${STAGING_BUCKET}" --quiet 2>/dev/null || echo "    Bucket not found or already deleted."

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                   Teardown Complete                         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
