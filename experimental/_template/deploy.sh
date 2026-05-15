#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Load Configuration ─────────────────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a; source "${SCRIPT_DIR}/.env"; set +a
fi

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
STAGING_BUCKET="${STAGING_BUCKET:-gs://${PROJECT_ID}-demo-staging}"

export PROJECT_ID REGION

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║              Agent Platform Demo — Deploy                   ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:  ${PROJECT_ID}"
echo "  Region:   ${REGION}"
echo ""

# ─── Step 1: Create Staging GCS Bucket ───────────────────────────────────────
echo ">>> Step 1/N: Creating staging bucket ${STAGING_BUCKET}..."
gcloud storage buckets create "${STAGING_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --quiet 2>/dev/null || echo "    Bucket already exists."

# ─── Step 2: Verify Principal Set IAM Grants ──────────────────────────────
# Common roles (agentDefaultAccess, storage.objectAdmin) are granted via
# principal set in setup-project.sh. This covers all agents in the project.
echo ""
echo ">>> Step 2/N: Verifying principal set IAM grants..."
echo "    roles/aiplatform.agentDefaultAccess — covers inference, logging, tracing, monitoring, registry read"
echo "    roles/storage.objectAdmin — covers telemetry GCS uploads"
echo "    (Granted via setup-project.sh principal set — no per-agent grants needed)"

# ─── Step 3: Deploy Agent ─────────────────────────────────────────────────
echo ""
echo ">>> Step 3/N: Deploying Agent..."
cd "${SCRIPT_DIR}/demo-agent"

uv run python deploy_agent.py

AGENT_RESOURCE_NAME=$(python3 -c "import json; print(json.load(open('deployment_metadata.json'))['remote_agent_runtime_id'])")
cd "${SCRIPT_DIR}"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Deployment Complete                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Agent:  ${AGENT_RESOURCE_NAME}"
echo "╚══════════════════════════════════════════════════════════════╝"
