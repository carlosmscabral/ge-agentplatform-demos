#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Load Configuration ─────────────────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a; source "${SCRIPT_DIR}/.env"; set +a
fi

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
STAGING_BUCKET="${PROJECT_ID}-evals-staging"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3-flash-preview}"

export PROJECT_ID REGION

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          Agent Platform Demo — Evals — Deploy               ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:  ${PROJECT_ID}"
echo "  Region:   ${REGION}"
echo "  Model:    ${GEMINI_MODEL}"
echo ""

# ─── Step 1: Create Staging GCS Bucket ───────────────────────────────────────
echo ">>> Step 1/2: Creating staging bucket gs://${STAGING_BUCKET}..."
gcloud storage buckets create "gs://${STAGING_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --quiet 2>/dev/null || echo "    Bucket already exists."

# ─── Step 2: Deploy Agent via agents-cli ─────────────────────────────────────
echo ""
echo ">>> Step 2/2: Deploying Agent via agents-cli..."
cd "${SCRIPT_DIR}/demo-agent"

agents-cli deploy \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --update-env-vars "GEMINI_MODEL=${GEMINI_MODEL},GOOGLE_CLOUD_LOCATION=global,LOGS_BUCKET_NAME=${STAGING_BUCKET},OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY,OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false" \
    --no-confirm-project

cd "${SCRIPT_DIR}"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Deployment Complete                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Next steps:                                                ║"
echo "║  1. Send queries via playground or agents-cli run           ║"
echo "║  2. Check traces: Console > Agent Platform > Agents         ║"
echo "║  3. Set up online monitor: Dashboard > Evaluation           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
