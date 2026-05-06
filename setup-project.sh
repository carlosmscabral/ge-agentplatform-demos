#!/bin/bash
set -euo pipefail

# One-time project setup for Agent Platform demos.
# Grants common IAM roles via principal set so every agent deployed
# in this project automatically gets the permissions it needs.
#
# Usage:
#   PROJECT_ID=vibe-cabral ./setup-project.sh
#
# What this does:
#   - Grants roles/aiplatform.agentDefaultAccess via principal set
#     (inference, logging, tracing, monitoring, registry read, service usage)
#   - Grants roles/storage.objectAdmin via principal set
#     (telemetry GCS uploads, staging buckets)
#
# After running this, individual demo deploy scripts don't need to grant
# per-agent IAM roles for standard operations. Only demo-specific roles
# (e.g., agentregistry.viewer for governance demo) need per-agent grants.

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
PROJECT_NUMBER="${PROJECT_NUMBER:-$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')}"

ORG_ID=$(gcloud projects get-ancestors "${PROJECT_ID}" --format='value(ID)' 2>/dev/null | tail -1)
if [ -z "${ORG_ID}" ]; then
    echo "ERROR: Could not determine organization ID for project ${PROJECT_ID}"
    exit 1
fi

PRINCIPAL_SET="principalSet://agents.global.org-${ORG_ID}.system.id.goog/attribute.platformContainer/aiplatform/projects/${PROJECT_NUMBER}"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║     Agent Platform Demos — Project Setup                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:       ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "  Organization:  ${ORG_ID}"
echo "  Principal Set: ${PRINCIPAL_SET}"
echo ""

ROLES=(
    "roles/aiplatform.agentDefaultAccess"
    "roles/storage.objectAdmin"
)

for ROLE in "${ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="${PRINCIPAL_SET}" \
        --role="${ROLE}" \
        --condition=None --quiet > /dev/null 2>&1 || true
    echo "  Granted ${ROLE}"
done

echo ""
echo "Done. All agents deployed in ${PROJECT_ID} now have:"
echo "  - Inference, logging, tracing, monitoring, registry read (agentDefaultAccess)"
echo "  - GCS storage access for telemetry uploads (storage.objectAdmin)"
echo ""
echo "Individual demos only need per-agent grants for sensitive resources."
