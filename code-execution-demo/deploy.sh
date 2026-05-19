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
STAGING_BUCKET="${STAGING_BUCKET:-${PROJECT_ID}-code-exec-staging}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3-flash-preview}"
ORCHESTRATOR_DISPLAY_NAME="${ORCHESTRATOR_DISPLAY_NAME:-code-analyst}"
SANDBOX_HOST_DISPLAY_NAME="${SANDBOX_HOST_DISPLAY_NAME:-code-analyst-sandbox-host}"
SANDBOX_DISPLAY_NAME="${SANDBOX_DISPLAY_NAME:-code-analyst-shared-sandbox}"
SANDBOX_TTL="${SANDBOX_TTL:-3600s}"

export PROJECT_ID REGION

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   code-execution-demo — Data Analyst Sandbox Deploy         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Project:        ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "  Region:         ${REGION}"
echo "  Model:          ${GEMINI_MODEL}"
echo "  Orchestrator:   ${ORCHESTRATOR_DISPLAY_NAME} (SPIFFE identity)"
echo "  Sandbox host:   ${SANDBOX_HOST_DISPLAY_NAME}"
echo "  Shared sandbox: ${SANDBOX_DISPLAY_NAME} (TTL ${SANDBOX_TTL})"
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
)

for ROLE in "${BASELINE_ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="${PRINCIPAL_SET}" --role="${ROLE}" \
        --condition=None --quiet > /dev/null 2>&1 || true
    echo "    ✓ ${ROLE}"
done

# ─── Step 3: uv lock orchestrator (Rule #6) ──────────────────────────────────
echo ""
echo ">>> Step 3/9: Refreshing uv.lock for analyst-agent..."
cd "${SCRIPT_DIR}/analyst-agent"
uv lock --quiet 2>/dev/null || true

# ─── Step 4: Sandbox-host Reasoning Engine pre-create ────────────────────────
echo ""
echo ">>> Step 4/9: Pre-creating sandbox-host Reasoning Engine..."
echo "    Why: AgentEngineSandboxCodeExecutor needs a Reasoning Engine to host"
echo "         sandboxes. If left to auto-create, each orchestrator instance"
echo "         would create its own → proliferation of orphan REs. We pre-create"
echo "         ONE dedicated 'sandbox host' RE and inject its name into the"
echo "         orchestrator via env var AGENT_ENGINE_RESOURCE_NAME."
# Idempotency: list existing reasoning engines via REST and reuse by displayName
# (there is no `gcloud ai reasoning-engines` command — see LEARNINGS.md L157).
ACCESS_TOKEN=$(gcloud auth print-access-token)
EXISTING_HOST=$(curl -s \
    "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_NUMBER}/locations/${REGION}/reasoningEngines?pageSize=200" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    | python3 -c "
import json, sys
data = json.load(sys.stdin)
for re in data.get('reasoningEngines', []):
    if re.get('displayName') == '${SANDBOX_HOST_DISPLAY_NAME}':
        print(re['name'])
        break
" 2>/dev/null || echo "")

if [ -n "${EXISTING_HOST}" ]; then
    SANDBOX_HOST="${EXISTING_HOST}"
    echo "    ✓ Reusing existing sandbox-host: ${SANDBOX_HOST}"
else
    echo "    Creating new sandbox-host via analyst-agent venv (has vertexai SDK)..."
    SANDBOX_HOST=$(uv --directory "${SCRIPT_DIR}/analyst-agent" run python - <<EOF
import sys
import vertexai
try:
    client = vertexai.Client(
        project="${PROJECT_ID}",
        location="${REGION}",
        http_options={"api_version": "v1beta1"},
    )
    result = client.agent_engines.create(
        config={"display_name": "${SANDBOX_HOST_DISPLAY_NAME}"},
    )
    print(result.api_resource.name)
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
EOF
    )
    if [ -z "${SANDBOX_HOST}" ]; then
        echo "  ✗ Failed to create sandbox-host RE — aborting."
        exit 1
    fi
    echo "    ✓ Created sandbox-host: ${SANDBOX_HOST}"
fi

cd "${SCRIPT_DIR}"

# ─── Step 5: Pre-create shared sandbox with short TTL ────────────────────────
echo ""
echo ">>> Step 5/10: Pre-creating shared sandbox (TTL=${SANDBOX_TTL})..."
echo "    Why: AgentEngineSandboxCodeExecutor's lazy-create path hardcodes"
echo "         ttl='31536000s' (1 year). Pre-creating with our own TTL gives"
echo "         lifecycle control. The sandbox is SHARED across sessions"
echo "         (Modo 1 of the executor) — state leaks between users but the"
echo "         lifetime is bounded. See ARCHITECTURE.md §2.4 for trade-offs."

# Idempotency: reuse if an existing sandbox under our host is still RUNNING
# AND not too close to expiry; else delete + create.
SANDBOX_RESOURCE=$(uv --directory "${SCRIPT_DIR}/analyst-agent" run python - <<EOF
import sys
from datetime import datetime, timedelta, timezone
import vertexai
from vertexai import types

client = vertexai.Client(
    project="${PROJECT_ID}",
    location="${REGION}",
    http_options={"api_version": "v1beta1"},
)
host = "${SANDBOX_HOST}"
display = "${SANDBOX_DISPLAY_NAME}"
ttl = "${SANDBOX_TTL}"

# Look for existing matching sandbox
existing = None
for sb in client.agent_engines.sandboxes.list(name=host):
    if sb.display_name == display and str(sb.state) == "State.STATE_RUNNING":
        # Require >= 5 minutes of remaining lifetime
        remaining = sb.expire_time - datetime.now(timezone.utc)
        if remaining > timedelta(minutes=5):
            existing = sb
            break
        else:
            # Too close to expiry — delete and recreate
            try:
                client.agent_engines.sandboxes.delete(name=sb.name)
            except Exception:
                pass

if existing:
    print(existing.name)
else:
    op = client.agent_engines.sandboxes.create(
        spec={"code_execution_environment": {}},
        name=host,
        config=types.CreateAgentEngineSandboxConfig(
            display_name=display,
            ttl=ttl,
        ),
    )
    print(op.response.name)
EOF
)

if [ -z "${SANDBOX_RESOURCE}" ]; then
    echo "  ✗ Failed to provision shared sandbox — aborting."
    exit 1
fi
echo "    ✓ Sandbox: ${SANDBOX_RESOURCE}"

# ─── Step 6: Deploy orchestrator with SPIFFE identity ────────────────────────
echo ""
echo ">>> Step 6/10: Deploying orchestrator (SPIFFE identity + shared sandbox)..."

ORCH_ENV_VARS="GEMINI_MODEL=${GEMINI_MODEL}"
ORCH_ENV_VARS="${ORCH_ENV_VARS},GOOGLE_CLOUD_LOCATION=global"
ORCH_ENV_VARS="${ORCH_ENV_VARS},GOOGLE_CLOUD_REGION=${REGION}"
ORCH_ENV_VARS="${ORCH_ENV_VARS},LOGS_BUCKET_NAME=${STAGING_BUCKET}"
ORCH_ENV_VARS="${ORCH_ENV_VARS},OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY"
ORCH_ENV_VARS="${ORCH_ENV_VARS},OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental"
ORCH_ENV_VARS="${ORCH_ENV_VARS},ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false"
ORCH_ENV_VARS="${ORCH_ENV_VARS},GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False"
ORCH_ENV_VARS="${ORCH_ENV_VARS},AGENT_ENGINE_RESOURCE_NAME=${SANDBOX_HOST}"
# Pre-created shared sandbox (preferred over agent_engine_resource_name in agent.py).
ORCH_ENV_VARS="${ORCH_ENV_VARS},SANDBOX_RESOURCE_NAME=${SANDBOX_RESOURCE}"

# `agents-cli deploy` spawns a local subprocess that imports app.agent to
# introspect operations. Export the env vars locally so the introspection
# sees the same config as the runtime (consistency).
export AGENT_ENGINE_RESOURCE_NAME="${SANDBOX_HOST}"
export SANDBOX_RESOURCE_NAME="${SANDBOX_RESOURCE}"

cd "${SCRIPT_DIR}/analyst-agent"
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
echo ">>> Step 7/10: Extracting orchestrator SPIFFE identity..."
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
    echo "  ⚠ SPIFFE ID not yet visible. Sandbox host IAM grant will be skipped."
else
    echo "  ✓ Orchestrator SPIFFE: ${ORCH_SPIFFE}"
fi

cd "${SCRIPT_DIR}"

# ─── Step 8: Verify orchestrator SPIFFE IAM coverage ─────────────────────────
echo ""
echo ">>> Step 8/10: Verifying SPIFFE IAM coverage for sandbox-host access..."
echo "    The Step 2 baseline grants project-wide roles/aiplatform.user to"
echo "    the SPIFFE principalSet, which covers agent_engines.sandboxes.* on"
echo "    ANY reasoning engine in the project (including the sandbox-host)."
echo "    No per-RE IAM binding needed."
if [ -n "${ORCH_SPIFFE}" ]; then
    echo "    ✓ Orchestrator SPIFFE inherits project-level aiplatform.user"
fi

# ─── Step 9: Save deploy state for undeploy ──────────────────────────────────
echo ""
echo ">>> Step 9/10: Persisting deploy state..."
cat > "${SCRIPT_DIR}/.deploy-state" <<EOF
SANDBOX_HOST_RESOURCE=${SANDBOX_HOST}
SANDBOX_RESOURCE=${SANDBOX_RESOURCE}
ORCH_RESOURCE=${ORCHESTRATOR_RESOURCE}
ORCH_SPIFFE=${ORCH_SPIFFE}
EOF
echo "    ✓ ${SCRIPT_DIR}/.deploy-state"

# ─── Step 10: Summary ────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Deployment Complete                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Orchestrator:    ${ORCHESTRATOR_RESOURCE}"
echo "║  Sandbox host:    ${SANDBOX_HOST}"
echo "║  Shared sandbox:  ${SANDBOX_RESOURCE}"
echo "║  Sandbox TTL:     ${SANDBOX_TTL}"
echo "║  SPIFFE:          ${ORCH_SPIFFE:-'(pending)'}"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Test the agent:"
echo "  cd analyst-agent && agents-cli run --url '${ORCHESTRATOR_URL}' --mode adk \\"
echo "    'Crie um DataFrame com 1000 vendas sintéticas (seed=42) e mostre .describe()'"
echo ""
echo "Then resume the session:"
echo "  agents-cli run --url '${ORCHESTRATOR_URL}' --mode adk --session-id <id> \\"
echo "    'Agora plote um histograma dos valores'"
echo ""
echo "View traces:"
echo "  https://console.cloud.google.com/traces/list?project=${PROJECT_ID}"
