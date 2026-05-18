#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a; source "${SCRIPT_DIR}/.env"; set +a
fi

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
PROJECT_NUMBER="${PROJECT_NUMBER:-$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')}"
REGION="${REGION:-us-central1}"
STAGING_BUCKET="${STAGING_BUCKET:-${PROJECT_ID}-code-exec-staging}"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   code-execution-demo — Undeploy (reverse cleanup)          ║"
echo "╚══════════════════════════════════════════════════════════════╝"

# ─── Load deploy state ────────────────────────────────────────────────────────
SANDBOX_HOST_RESOURCE=""
ORCH_RESOURCE=""
ORCH_SPIFFE=""
if [ -f "${SCRIPT_DIR}/.deploy-state" ]; then
    set -a; source "${SCRIPT_DIR}/.deploy-state"; set +a
fi

# Fallback: read orchestrator from deployment_metadata.json if state missing.
META="${SCRIPT_DIR}/analyst-agent/deployment_metadata.json"
if [ -z "${ORCH_RESOURCE}" ] && [ -f "${META}" ]; then
    ORCH_RESOURCE=$(python3 -c "import json; print(json.load(open('${META}'))['remote_agent_runtime_id'])" 2>/dev/null || echo "")
fi

# ─── Step 1: Delete orchestrator agent ───────────────────────────────────────
echo ""
echo ">>> Step 1/5: Deleting orchestrator agent..."
if [ -n "${ORCH_RESOURCE}" ]; then
    ORCH_RE_ID=$(echo "${ORCH_RESOURCE}" | grep -oP 'reasoningEngines/\K[0-9]+' || echo "")
    if [ -n "${ORCH_RE_ID}" ]; then
        echo "  Deleting ${ORCH_RESOURCE}..."
        curl -s -X DELETE \
            "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines/${ORCH_RE_ID}?force=true" \
            -H "Authorization: Bearer $(gcloud auth print-access-token)" > /dev/null
        echo "    ✓ orchestrator deleted"
    fi
    rm -f "${META}"
else
    echo "  (no orchestrator resource recorded — nothing to delete)"
fi

# ─── Step 2: Delete sandbox-host Reasoning Engine ────────────────────────────
echo ""
echo ">>> Step 2/5: Deleting sandbox-host Reasoning Engine..."
if [ -n "${SANDBOX_HOST_RESOURCE}" ]; then
    SANDBOX_HOST_RE_ID=$(echo "${SANDBOX_HOST_RESOURCE}" | grep -oP 'reasoningEngines/\K[0-9]+' || echo "")
    if [ -n "${SANDBOX_HOST_RE_ID}" ]; then
        echo "  Deleting ${SANDBOX_HOST_RESOURCE} (force=true — sweeps sandboxes too)..."
        curl -s -X DELETE \
            "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines/${SANDBOX_HOST_RE_ID}?force=true" \
            -H "Authorization: Bearer $(gcloud auth print-access-token)" > /dev/null
        echo "    ✓ sandbox-host deleted"
    fi
else
    echo "  (no sandbox-host recorded — nothing to delete)"
fi

# ─── Step 3: SPIFFE IAM is project-level — no per-RE revoke needed ───────────
echo ""
echo ">>> Step 3/5: No per-RE IAM grants to revoke."
echo "    Baseline principalSet IAM applies project-wide and is shared across"
echo "    all SPIFFE-bound agents — leave it intact for other demos."

# ─── Step 4: Delete staging bucket ───────────────────────────────────────────
echo ""
echo ">>> Step 4/5: Deleting staging bucket gs://${STAGING_BUCKET}..."
gcloud storage rm --recursive "gs://${STAGING_BUCKET}" --quiet 2>/dev/null \
    && echo "    ✓ bucket deleted" \
    || echo "    (bucket not found or already empty)"

# ─── Step 5: Local cleanup ───────────────────────────────────────────────────
echo ""
echo ">>> Step 5/5: Local cleanup..."
rm -f "${SCRIPT_DIR}/analyst-agent/.requirements.txt"
rm -f "${SCRIPT_DIR}/analyst-agent/deployment_metadata.json"
rm -f "${SCRIPT_DIR}/.deploy-state"
echo "    ✓ local artifacts removed"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Undeploy Complete                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
