#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NO_CONFIRM="false"
for arg in "$@"; do
    case "$arg" in
        --no-confirm) NO_CONFIRM="true" ;;
    esac
done

# ─── Load Configuration ─────────────────────────────────────────────────────
if [ -f "${SCRIPT_DIR}/.env" ]; then
    set -a; source "${SCRIPT_DIR}/.env"; set +a
fi

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
PROJECT_NUMBER="${PROJECT_NUMBER:-$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')}"
REGION="${REGION:-us-central1}"
STAGING_BUCKET="${STAGING_BUCKET:-${PROJECT_ID}-oauth-3lo-staging}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3-flash-preview}"
AGENT_DISPLAY_NAME="${AGENT_DISPLAY_NAME:-oauth-3lo-agent}"
AUTH_PROVIDER_NAME="${AUTH_PROVIDER_NAME:-oauth-3lo-keycloak}"
AUTH_PROVIDER_LOCATION="${AUTH_PROVIDER_LOCATION:-us-central1}"
MCP_SERVICE_NAME="${MCP_SERVICE_NAME:-oauth-3lo-mcp}"
MCP_REGISTRY_DISPLAY_NAME="${MCP_REGISTRY_DISPLAY_NAME:-oauth-3lo-mcp}"
FRONTEND_SERVICE_NAME="${FRONTEND_SERVICE_NAME:-oauth-3lo-frontend}"

# Required Keycloak inputs
: "${KEYCLOAK_URL:?KEYCLOAK_URL must be set in .env}"
: "${KEYCLOAK_REALM:?KEYCLOAK_REALM must be set in .env}"
: "${KEYCLOAK_CLIENT_ID:?KEYCLOAK_CLIENT_ID must be set in .env}"
: "${KEYCLOAK_CLIENT_SECRET:?KEYCLOAK_CLIENT_SECRET must be set in .env}"
KEYCLOAK_URL="${KEYCLOAK_URL%/}"
KEYCLOAK_AUDIENCE="${KEYCLOAK_AUDIENCE:-account}"
KEYCLOAK_VERIFY_AUDIENCE="${KEYCLOAK_VERIFY_AUDIENCE:-true}"

KEYCLOAK_AUTH_URL="${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/auth"
KEYCLOAK_TOKEN_URL="${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token"

AUTH_PROVIDER_FULL_NAME="projects/${PROJECT_ID}/locations/${AUTH_PROVIDER_LOCATION}/connectors/${AUTH_PROVIDER_NAME}"

export PROJECT_ID REGION

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Agent Platform OAuth 3LO + Keycloak Demo — Deploy         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:        ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "  Region:         ${REGION}"
echo "  Model:          ${GEMINI_MODEL}"
echo "  Agent:          ${AGENT_DISPLAY_NAME}"
echo "  MCP service:    ${MCP_SERVICE_NAME}"
echo "  Frontend:       ${FRONTEND_SERVICE_NAME}"
echo "  Auth provider:  ${AUTH_PROVIDER_FULL_NAME}"
echo "  Keycloak:       ${KEYCLOAK_URL} (realm=${KEYCLOAK_REALM})"
echo ""

# ─── Step 1: Enable APIs ────────────────────────────────────────────────────
echo ">>> Step 1/11: Enabling required APIs…"
gcloud services enable \
    aiplatform.googleapis.com \
    iamconnectors.googleapis.com \
    iamconnectorcredentials.googleapis.com \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    cloudapiregistry.googleapis.com \
    artifactregistry.googleapis.com \
    --project="${PROJECT_ID}" --quiet

# ─── Step 2: Create staging GCS bucket ──────────────────────────────────────
echo ""
echo ">>> Step 2/11: Creating staging bucket gs://${STAGING_BUCKET}…"
gcloud storage buckets create "gs://${STAGING_BUCKET}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --quiet 2>/dev/null || echo "    Bucket already exists."

# ─── Step 3: Grant baseline IAM (principal set) ─────────────────────────────
echo ""
echo ">>> Step 3/11: Granting baseline IAM roles to all SPIFFE agents…"
ORG_ID_LOCAL=$(gcloud organizations list --format='value(ID)' --limit=1 2>/dev/null || echo "")
if [ -z "${ORG_ID_LOCAL}" ]; then
    PRINCIPAL_SET="principalSet://agents.global.project-${PROJECT_NUMBER}.system.id.goog/attribute.platformContainer/aiplatform/projects/${PROJECT_NUMBER}"
else
    PRINCIPAL_SET="principalSet://agents.global.org-${ORG_ID_LOCAL}.system.id.goog/attribute.platformContainer/aiplatform/projects/${PROJECT_NUMBER}"
fi
echo "  Principal set: ${PRINCIPAL_SET}"

BASELINE_ROLES=(
    "roles/aiplatform.agentDefaultAccess"
    "roles/aiplatform.user"
    "roles/aiplatform.agentContextEditor"
    "roles/serviceusage.serviceUsageConsumer"
    "roles/logging.logWriter"
    "roles/monitoring.metricWriter"
    "roles/cloudapiregistry.viewer"
    "roles/agentregistry.viewer"
    "roles/storage.objectAdmin"
    "roles/iamconnectors.user"
)

for ROLE in "${BASELINE_ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="${PRINCIPAL_SET}" \
        --role="${ROLE}" \
        --condition=None --quiet > /dev/null 2>&1 || true
    echo "    ✓ ${ROLE}"
done

# ─── Step 4: Deploy MCP server to Cloud Run ─────────────────────────────────
echo ""
echo ">>> Step 4/11: Deploying MCP server to Cloud Run…"
gcloud run deploy "${MCP_SERVICE_NAME}" \
    --source="${SCRIPT_DIR}/mcp-server" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --allow-unauthenticated \
    --port=8080 \
    --set-env-vars="KEYCLOAK_URL=${KEYCLOAK_URL},KEYCLOAK_REALM=${KEYCLOAK_REALM},KEYCLOAK_AUDIENCE=${KEYCLOAK_AUDIENCE},KEYCLOAK_VERIFY_AUDIENCE=${KEYCLOAK_VERIFY_AUDIENCE}" \
    --quiet

MCP_URL=$(gcloud run services describe "${MCP_SERVICE_NAME}" \
    --region="${REGION}" --project="${PROJECT_ID}" \
    --format='value(status.url)')
echo "  MCP URL: ${MCP_URL}"

# ─── Step 5: Register MCP server in Agent Registry ──────────────────────────
echo ""
echo ">>> Step 5/11: Registering MCP server in Agent Registry…"
TOOLSPEC_CONTENT=$(cat "${SCRIPT_DIR}/mcp-server/toolspec.json")
# Tags via gcloud aren't exposed for Agent Registry services (as of 2026-05);
# we encode them in the description as [tag:X] for downstream discovery.
REGISTRY_DESCRIPTION="[tag:identity] [tag:oauth] [tag:keycloak] [tag:3lo] [domain:auth-demo] Keycloak-protected MCP server demonstrating Agent Identity 3-Legged OAuth."

if gcloud alpha agent-registry services describe "${MCP_REGISTRY_DISPLAY_NAME}" \
        --location="${REGION}" --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
    echo "    Service exists — updating…"
    gcloud alpha agent-registry services update "${MCP_REGISTRY_DISPLAY_NAME}" \
        --location="${REGION}" --project="${PROJECT_ID}" \
        --description="${REGISTRY_DESCRIPTION}" \
        --interfaces="protocolBinding=jsonrpc,url=${MCP_URL}/mcp" \
        --mcp-server-spec-type=tool-spec \
        --mcp-server-spec-content="${TOOLSPEC_CONTENT}" \
        --quiet 2>&1 | tail -3
else
    gcloud alpha agent-registry services create "${MCP_REGISTRY_DISPLAY_NAME}" \
        --location="${REGION}" --project="${PROJECT_ID}" \
        --display-name="${MCP_REGISTRY_DISPLAY_NAME}" \
        --description="${REGISTRY_DESCRIPTION}" \
        --interfaces="protocolBinding=jsonrpc,url=${MCP_URL}/mcp" \
        --mcp-server-spec-type=tool-spec \
        --mcp-server-spec-content="${TOOLSPEC_CONTENT}" \
        --quiet
fi

# `services create` returns immediately but the mirror resource in
# mcp-servers/ propagates asynchronously (a few seconds). Retry until visible.
MCP_REGISTRY_NAME=""
for i in 1 2 3 4 5 6 7 8 9 10; do
    MCP_REGISTRY_NAME=$(gcloud alpha agent-registry mcp-servers list \
        --location="${REGION}" --project="${PROJECT_ID}" \
        --filter="displayName='${MCP_REGISTRY_DISPLAY_NAME}'" \
        --format='value(name)' 2>/dev/null | head -1)
    if [ -n "${MCP_REGISTRY_NAME}" ]; then
        break
    fi
    echo "    Waiting for mcp-servers/ to mirror the new service (attempt ${i}/10)…"
    sleep 5
done

if [ -z "${MCP_REGISTRY_NAME}" ]; then
    echo "  ⚠ Could not resolve MCP registry name after 50s — aborting."
    exit 1
fi
echo "  MCP registry resource: ${MCP_REGISTRY_NAME}"

# ─── Step 6: Create/Update Agent Identity auth provider ─────────────────────
echo ""
echo ">>> Step 6/11: Creating/updating Agent Identity 3LO auth provider…"
# `--allowed-scopes` is REQUIRED for OIDC IdPs: when unset, the auth request
# omits `scope` entirely and Keycloak (and any spec-compliant OIDC IdP) rejects
# with `invalid_scope`. openid+profile+email is the standard OIDC trio.
ALLOWED_SCOPES="${ALLOWED_SCOPES:-openid,profile,email}"

# `undeploy.sh` does a soft-delete (30-day retention; no --purge flag exists).
# A connector in soft-deleted state will appear in `describe` but `update`
# returns NOT_FOUND and `create` returns ALREADY_EXISTS. The fix: always try
# `undelete` first (no-op if already active or never deleted), then decide
# create-vs-update from there.
gcloud alpha agent-identity connectors undelete "${AUTH_PROVIDER_NAME}" \
    --location="${AUTH_PROVIDER_LOCATION}" \
    --project="${PROJECT_ID}" --quiet >/dev/null 2>&1 \
    && echo "    Restored soft-deleted connector from prior undeploy"

if gcloud alpha agent-identity connectors describe "${AUTH_PROVIDER_NAME}" \
        --location="${AUTH_PROVIDER_LOCATION}" --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
    echo "    Connector exists — updating allowed scopes…"
    gcloud alpha agent-identity connectors update "${AUTH_PROVIDER_NAME}" \
        --location="${AUTH_PROVIDER_LOCATION}" \
        --project="${PROJECT_ID}" \
        --allowed-scopes="${ALLOWED_SCOPES}" \
        --quiet 2>&1 | tail -3
else
    gcloud alpha agent-identity connectors create "${AUTH_PROVIDER_NAME}" \
        --location="${AUTH_PROVIDER_LOCATION}" \
        --project="${PROJECT_ID}" \
        --three-legged-oauth-authorization-url="${KEYCLOAK_AUTH_URL}" \
        --three-legged-oauth-token-url="${KEYCLOAK_TOKEN_URL}" \
        --allowed-scopes="${ALLOWED_SCOPES}" \
        --quiet 2>&1 | tail -3
fi

REDIRECT_URL=$(gcloud alpha agent-identity connectors describe "${AUTH_PROVIDER_NAME}" \
    --location="${AUTH_PROVIDER_LOCATION}" \
    --project="${PROJECT_ID}" \
    --format='value(connectorTypeParams.threeLeggedOauth.redirectUrl)' 2>/dev/null || echo "")

echo ""
echo "  ┌───────────────────────────────────────────────────────────────────────┐"
echo "  │   ACTION REQUIRED — register this callback URL in Keycloak             │"
echo "  ├───────────────────────────────────────────────────────────────────────┤"
echo "  │   1) Open  ${KEYCLOAK_URL}/admin/"
echo "  │   2) Realm dropdown → '${KEYCLOAK_REALM}'                              │"
echo "  │   3) Clients → '${KEYCLOAK_CLIENT_ID}' → Settings tab                  │"
echo "  │   4) Access settings → 'Valid Redirect URIs' → add the URL below       │"
echo "  │   5) Save                                                              │"
echo "  └───────────────────────────────────────────────────────────────────────┘"
echo ""
echo "    URL to paste:"
echo "      ${REDIRECT_URL}"
echo ""

if [ "${NO_CONFIRM}" != "true" ]; then
    read -r -p "    Press ENTER once the redirect URL is registered in Keycloak… " _
fi

# ─── Step 7: Update auth provider with Keycloak client_id/secret ────────────
echo ""
echo ">>> Step 7/11: Updating auth provider with Keycloak client credentials…"
gcloud alpha agent-identity connectors update "${AUTH_PROVIDER_NAME}" \
    --location="${AUTH_PROVIDER_LOCATION}" \
    --project="${PROJECT_ID}" \
    --three-legged-oauth-client-id="${KEYCLOAK_CLIENT_ID}" \
    --three-legged-oauth-client-secret="${KEYCLOAK_CLIENT_SECRET}" \
    --quiet
echo "    ✓ Client credentials applied"

# ─── Step 8: Deploy frontend stub (gives us CONTINUE_URI) ───────────────────
echo ""
echo ">>> Step 8/11: Deploying frontend stub to Cloud Run (to obtain CONTINUE_URI)…"
gcloud run deploy "${FRONTEND_SERVICE_NAME}" \
    --source="${SCRIPT_DIR}/frontend" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --allow-unauthenticated \
    --port=8080 \
    --set-env-vars="PROJECT_ID=${PROJECT_ID},REGION=${REGION},AUTH_PROVIDER_FULL_NAME=${AUTH_PROVIDER_FULL_NAME},AGENT_ENGINE_ID=" \
    --quiet

# After first deploy, derive CANONICAL_URL and inject it so the cookie/host
# middleware redirects the project-number alternate URL onto the canonical one.
FRONTEND_URL_TMP=$(gcloud run services describe "${FRONTEND_SERVICE_NAME}" \
    --region="${REGION}" --project="${PROJECT_ID}" --format='value(status.url)')
gcloud run services update "${FRONTEND_SERVICE_NAME}" \
    --region="${REGION}" --project="${PROJECT_ID}" \
    --update-env-vars="CANONICAL_URL=${FRONTEND_URL_TMP}" \
    --quiet > /dev/null

FRONTEND_URL=$(gcloud run services describe "${FRONTEND_SERVICE_NAME}" \
    --region="${REGION}" --project="${PROJECT_ID}" \
    --format='value(status.url)')
CONTINUE_URI="${FRONTEND_URL}/validateUserId"
echo "  Frontend URL:  ${FRONTEND_URL}"
echo "  CONTINUE_URI:  ${CONTINUE_URI}"

# ─── Step 9: Deploy the ADK agent with SPIFFE identity ──────────────────────
echo ""
echo ">>> Step 9/11: Deploying ADK agent (SPIFFE + 3LO)…"
cd "${SCRIPT_DIR}/agent"
uv lock --quiet 2>/dev/null || true

agents-cli deploy \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --agent-identity \
    --update-env-vars "GEMINI_MODEL=${GEMINI_MODEL},GOOGLE_CLOUD_LOCATION=global,GOOGLE_CLOUD_REGION=${REGION},REGISTRY_LOCATION=${REGION},DISABLE_GCP_TELEMETRY=true,GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=False,GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False,CONTINUE_URI=${CONTINUE_URI},MCP_REGISTRY_NAME=${MCP_REGISTRY_NAME}" \
    --no-confirm-project

AGENT_RESOURCE=$(python3 -c "import json; print(json.load(open('deployment_metadata.json'))['remote_agent_runtime_id'])")
AGENT_ENGINE_ID=$(echo "${AGENT_RESOURCE}" | grep -oP 'reasoningEngines/\K[0-9]+')
AGENT_URL="https://${REGION}-aiplatform.googleapis.com/v1beta1/${AGENT_RESOURCE}"
echo ""
echo "  Agent deployed: ${AGENT_RESOURCE}"
echo "  Agent URL:      ${AGENT_URL}"

# Extract SPIFFE identity (best-effort, may take a few minutes)
ACCESS_TOKEN=$(gcloud auth print-access-token)
AGENT_SPIFFE=""
for i in 1 2 3 4 5; do
    AGENT_SPIFFE=$(curl -s \
        "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines/${AGENT_ENGINE_ID}" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('spec',{}).get('effectiveIdentity',''))" 2>/dev/null || echo "")
    if [ -n "${AGENT_SPIFFE}" ]; then
        break
    fi
    echo "    Waiting for SPIFFE identity (attempt ${i}/5)…"
    sleep 10
done
if [ -n "${AGENT_SPIFFE}" ]; then
    echo "  ✓ Agent SPIFFE: ${AGENT_SPIFFE}"
else
    echo "  ⚠ SPIFFE ID not yet available (this is fine — IAM is via principal set)"
fi

cd "${SCRIPT_DIR}"

# Persist deployment metadata for undeploy
python3 -c "
import json, sys
data = {
    'agent_resource_name': '${AGENT_RESOURCE}',
    'agent_engine_id': '${AGENT_ENGINE_ID}',
    'mcp_url': '${MCP_URL}',
    'mcp_registry_name': '${MCP_REGISTRY_NAME}',
    'auth_provider_full_name': '${AUTH_PROVIDER_FULL_NAME}',
    'frontend_url': '${FRONTEND_URL}',
    'staging_bucket': '${STAGING_BUCKET}',
}
json.dump(data, open('${SCRIPT_DIR}/deployment_metadata.json', 'w'), indent=2)
"

# ─── Step 10: Create the Agent Registry Binding ─────────────────────────────
# The binding ties (agent → MCP server) to (auth_provider) so the agent code
# doesn't need to know which connector to use. ADK reads this automatically at
# `get_mcp_toolset()` time.
echo ""
echo ">>> Step 10/12: Creating Agent Registry Binding (agent ⇄ MCP ⇄ auth_provider)…"

# Resolve URNs (these only exist after the agent and MCP are registered)
AGENT_URN=$(gcloud alpha agent-registry agents list \
    --location="${REGION}" --project="${PROJECT_ID}" \
    --filter="agentId:'reasoningEngines:${AGENT_ENGINE_ID}'" \
    --format='value(agentId)' 2>/dev/null | head -1)
MCP_URN=$(gcloud alpha agent-registry mcp-servers list \
    --location="${REGION}" --project="${PROJECT_ID}" \
    --filter="displayName='${MCP_REGISTRY_DISPLAY_NAME}'" \
    --format='value(mcpServerId)' 2>/dev/null | head -1)

if [ -z "${AGENT_URN}" ] || [ -z "${MCP_URN}" ]; then
    echo "  ⚠ Could not resolve URNs (agent=${AGENT_URN:-?}, mcp=${MCP_URN:-?}). Skipping binding."
else
    BINDING_NAME="${BINDING_NAME:-${AGENT_DISPLAY_NAME}-binding}"
    echo "  Source (agent): ${AGENT_URN}"
    echo "  Target (MCP):   ${MCP_URN}"
    echo "  Auth provider:  ${AUTH_PROVIDER_FULL_NAME}"
    echo "  Continue URI:   ${CONTINUE_URI}"

    if gcloud alpha agent-registry bindings describe "${BINDING_NAME}" \
            --location="${REGION}" --project="${PROJECT_ID}" --quiet >/dev/null 2>&1; then
        echo "  Binding exists — updating…"
        gcloud alpha agent-registry bindings update "${BINDING_NAME}" \
            --location="${REGION}" --project="${PROJECT_ID}" \
            --auth-provider-binding="${AUTH_PROVIDER_FULL_NAME}" \
            --auth-provider-binding-continue-uri="${CONTINUE_URI}" \
            --auth-provider-binding-scopes="${ALLOWED_SCOPES}" \
            --quiet 2>&1 | tail -3
    else
        gcloud alpha agent-registry bindings create "${BINDING_NAME}" \
            --location="${REGION}" --project="${PROJECT_ID}" \
            --source-identifier="${AGENT_URN}" \
            --target-identifier="${MCP_URN}" \
            --auth-provider-binding="${AUTH_PROVIDER_FULL_NAME}" \
            --auth-provider-binding-continue-uri="${CONTINUE_URI}" \
            --auth-provider-binding-scopes="${ALLOWED_SCOPES}" \
            --quiet 2>&1 | tail -3
    fi
    echo "    ✓ Binding ${BINDING_NAME} applied"

    # Also grant `roles/iamconnectors.user` on the CONNECTOR resource to the
    # agent's INDIVIDUAL principal (not just the principalSet). The Console
    # Identity tab for the agent shows Auth Providers based on per-principal
    # IAM on the connector — without this, the binding shows up but the
    # Auth Provider column is blank.
    INDIVIDUAL_PRINCIPAL="principal://agents.global.org-${ORG_ID_LOCAL}.system.id.goog/resources/aiplatform/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines/${AGENT_ENGINE_ID}"
    ACCESS_TOKEN=$(gcloud auth print-access-token)
    CURRENT_USER="user:$(gcloud config get-value account 2>/dev/null)"
    curl -s -X POST \
        "https://iamconnectors.googleapis.com/v1alpha/${AUTH_PROVIDER_FULL_NAME}:setIamPolicy" \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" \
        -H "x-goog-user-project: ${PROJECT_ID}" \
        -H "Content-Type: application/json" \
        -d "{\"policy\":{\"bindings\":[{\"role\":\"roles/iamconnectors.user\",\"members\":[\"${PRINCIPAL_SET}\",\"${INDIVIDUAL_PRINCIPAL}\",\"${CURRENT_USER}\"]}]}}" \
        > /dev/null && echo "    ✓ Connector IAM updated (binding + individual principal visible in Console)"
fi

# ─── Step 11: Redeploy frontend with AGENT_ENGINE_ID ────────────────────────
echo ""
echo ">>> Step 11/12: Redeploying frontend with AGENT_ENGINE_ID=${AGENT_ENGINE_ID}…"
gcloud run services update "${FRONTEND_SERVICE_NAME}" \
    --region="${REGION}" --project="${PROJECT_ID}" \
    --update-env-vars="AGENT_ENGINE_ID=${AGENT_ENGINE_ID}" \
    --quiet
echo "    ✓ Frontend now points at agent ${AGENT_ENGINE_ID}"

# ─── Step 12: Optional Gemini Enterprise registration ──────────────────────
if [ -n "${GEMINI_ENTERPRISE_APP_ID:-}" ]; then
    echo ""
    echo ">>> Step 12/12: Registering with Gemini Enterprise…"
    cd "${SCRIPT_DIR}/agent"
    agents-cli publish gemini-enterprise \
        --gemini-enterprise-app-id "${GEMINI_ENTERPRISE_APP_ID}" \
        --display-name "${GEMINI_DISPLAY_NAME:-${AGENT_DISPLAY_NAME}}" \
        --description "${GEMINI_DESCRIPTION:-OAuth 3LO + Keycloak demo agent}" \
        --no-confirm-project \
        || echo "    GE registration failed (non-blocking)."
    cd "${SCRIPT_DIR}"
else
    echo ""
    echo ">>> Step 12/12: Skipping Gemini Enterprise registration (GEMINI_ENTERPRISE_APP_ID not set)."
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Deployment Complete                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  MCP service:   ${MCP_URL}"
echo "║  MCP registry:  ${MCP_REGISTRY_NAME}"
echo "║  Auth provider: ${AUTH_PROVIDER_FULL_NAME}"
echo "║  Agent:         ${AGENT_RESOURCE}"
echo "║  SPIFFE:        ${AGENT_SPIFFE:-'(pending)'}"
echo "║  Frontend:      ${FRONTEND_URL}"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Open the frontend to drive the 3LO consent flow:"
echo "  ${FRONTEND_URL}"
echo ""
echo "Or use the Console Playground (it implements the consent handshake natively):"
echo "  https://console.cloud.google.com/vertex-ai/agents/locations/${REGION}/reasoning-engines/${AGENT_ENGINE_ID}?project=${PROJECT_ID}"
echo ""
echo "Sample prompt:"
echo "  'Qual é o meu perfil no sistema?'"
