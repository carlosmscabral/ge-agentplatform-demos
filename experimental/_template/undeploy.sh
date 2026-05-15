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
echo "║              Agent Platform Demo — Teardown                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:  ${PROJECT_ID}"
echo "  Region:   ${REGION}"
echo ""

# ─── Step 1: Delete ADK Agent ──────────────────────────────────────────────
echo ">>> Step 1/1: Deleting ADK Agent from Agent Runtime..."
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

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                   Teardown Complete                         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
