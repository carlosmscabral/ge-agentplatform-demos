#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a; source "${SCRIPT_DIR}/.env"; set +a
fi

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
PROJECT_NUMBER="${PROJECT_NUMBER:-$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')}"
REGION="${REGION:-us-central1}"
REGISTRY_LOCATION="${REGISTRY_LOCATION:-us-central1}"
STAGING_BUCKET="${STAGING_BUCKET:-${PROJECT_ID}-mcp-discovery-staging}"

MARKET_MCP_SERVICE="${MARKET_MCP_SERVICE:-fintoolkit-market-data-mcp}"
PORTFOLIO_MCP_SERVICE="${PORTFOLIO_MCP_SERVICE:-fintoolkit-portfolio-mcp}"
NEWS_MCP_SERVICE="${NEWS_MCP_SERVICE:-fintoolkit-news-sentiment-mcp}"

ALL_SVCS=("${MARKET_MCP_SERVICE}" "${PORTFOLIO_MCP_SERVICE}" "${NEWS_MCP_SERVICE}")

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   mcp-discovery-demo — Undeploy (reverse cleanup)           ║"
echo "╚══════════════════════════════════════════════════════════════╝"

# ─── Step 1: Delete orchestrator agent (Reasoning Engine) ────────────────────
echo ""
echo ">>> Step 1/6: Deleting orchestrator agent..."
META="${SCRIPT_DIR}/orchestrator-agent/deployment_metadata.json"
if [ -f "${META}" ]; then
    ORCH_RESOURCE=$(python3 -c "import json; print(json.load(open('${META}'))['remote_agent_runtime_id'])" 2>/dev/null || echo "")
    ORCH_RE_ID=$(echo "${ORCH_RESOURCE}" | grep -oP 'reasoningEngines/\K[0-9]+' || echo "")
    if [ -n "${ORCH_RE_ID}" ]; then
        echo "  Deleting reasoningEngines/${ORCH_RE_ID}..."
        curl -s -X DELETE \
            "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines/${ORCH_RE_ID}?force=true" \
            -H "Authorization: Bearer $(gcloud auth print-access-token)" > /dev/null
        echo "    ✓ orchestrator deleted"
    fi
    rm -f "${META}"
else
    echo "  (no deployment_metadata.json — nothing to delete)"
fi

# ─── Step 2: Revoke SPIFFE run.invoker grants ────────────────────────────────
echo ""
echo ">>> Step 2/6: Revoking SPIFFE roles/run.invoker grants on MCP services..."
STATE="${SCRIPT_DIR}/.deploy-state"
if [ -f "${STATE}" ]; then
    ORCH_SPIFFE=$(cat "${STATE}")
    if [ -n "${ORCH_SPIFFE}" ]; then
        for SVC in "${ALL_SVCS[@]}"; do
            gcloud run services remove-iam-policy-binding "${SVC}" \
                --member="principal://${ORCH_SPIFFE}" \
                --role="roles/run.invoker" \
                --region="${REGION}" --quiet > /dev/null 2>&1 || true
            echo "    ✓ revoked on ${SVC}"
        done
    fi
    rm -f "${STATE}"
else
    echo "  (no .deploy-state — skipping IAM revoke)"
fi

# ─── Step 3: Delete Agent Registry MCP server entries ────────────────────────
echo ""
echo ">>> Step 3/6: Deleting Agent Registry MCP server entries..."
for SVC in "${ALL_SVCS[@]}"; do
    gcloud alpha agent-registry services delete "${SVC}" \
        --location="${REGISTRY_LOCATION}" --quiet 2>/dev/null \
        && echo "    ✓ ${SVC}" \
        || echo "    (${SVC} not found)"
done

# ─── Step 4: Delete Cloud Run MCP services ───────────────────────────────────
echo ""
echo ">>> Step 4/6: Deleting Cloud Run MCP services..."
for SVC in "${ALL_SVCS[@]}"; do
    gcloud run services delete "${SVC}" \
        --region="${REGION}" --quiet 2>/dev/null \
        && echo "    ✓ ${SVC}" \
        || echo "    (${SVC} not found)"
done

# ─── Step 5: Delete staging bucket ───────────────────────────────────────────
echo ""
echo ">>> Step 5/6: Deleting staging bucket gs://${STAGING_BUCKET}..."
gcloud storage rm --recursive "gs://${STAGING_BUCKET}" --quiet 2>/dev/null \
    && echo "    ✓ bucket deleted" \
    || echo "    (bucket not found or already empty)"

# ─── Step 6: Local cleanup ───────────────────────────────────────────────────
echo ""
echo ">>> Step 6/6: Local cleanup..."
rm -f "${SCRIPT_DIR}/orchestrator-agent/.requirements.txt"
rm -f "${SCRIPT_DIR}/orchestrator-agent/deployment_metadata.json"
rm -f "${SCRIPT_DIR}/.deploy-state"
echo "    ✓ local artifacts removed"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Undeploy Complete                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
