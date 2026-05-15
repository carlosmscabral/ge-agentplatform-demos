# Agent Platform Demos — Development Standards

This file defines the production rules for all demos at the repository root. Demos under `experimental/` are exempt from these rules but should aim to conform for promotion. These rules are enforced by Claude Code when assisting with development.

---

## The 11 Rules

### Rule #1 — Full Parameterization

Every demo must be fully driven by environment variables so any user can run it with their own GCP project.

- `.env.template` documents every variable with defaults and descriptions
- Shell scripts auto-detect where possible:
  ```bash
  PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
  REGION="${REGION:-us-central1}"
  STAGING_BUCKET="${STAGING_BUCKET:-gs://${PROJECT_ID}-<demo>-staging}"
  ```
- Python code uses `os.environ.get()` with defaults — zero hardcoded project IDs, regions, or resource names
- No user-specific values (project names, org IDs, account emails) in committed code

### Rule #2 — agents-cli First

`agents-cli` is the primary tool for scaffolding, building, and deploying agents. Always start here.

- **New demos**: `agents-cli scaffold create` then `agents-cli scaffold enhance`
- **Deploy**: `agents-cli deploy` with `--update-env-vars` for telemetry and config
- **If agents-cli lacks a feature** (e.g., `context_spec` for Memory Bank, `agentGatewayConfig` for Agent Gateway): start with agents-cli, then adapt with a `deploy_agent.py` fallback. Document the gap clearly in `deploy.sh` and `ARCHITECTURE.md`
- Always load `google-agents-cli-*` skills when working on ADK development

### Rule #3 — Local Testing First

Deployments to Agent Runtime take 5-10 minutes. Minimize wasted cycles by testing locally first.

1. **Unit test** individual tools and functions with pytest
2. **Smoke test** the agent locally with `agents-cli run "test prompt"`
3. **Only then** deploy to cloud and test granularly
4. Full demo deployment is the last step, not the first

### Rule #4 — Full Telemetry

Every deployed agent must have full payload logging (prompts, responses, tool calls) unless explicitly stated otherwise.

Required env vars at deploy time (via `--update-env-vars` or `deploy_agent.py`):
```
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY
OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false
GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true
LOGS_BUCKET_NAME=<staging-bucket>
```

In `telemetry.py`: use `os.environ.setdefault()` — never hard-override the capture content mode so deploy-time values take precedence.

### Rule #5 — No Stale Entries

No checked-in files with pinned dependency versions or deployment artifacts.

- Dependencies are managed via `pyproject.toml` with version ranges
- `uv.lock` is acceptable for reproducibility
- `.requirements.txt` and `deployment_metadata.json` are gitignored and generated at deploy time
- Each user/run resolves dependencies independently

### Rule #6 — Consistent Deploy/Undeploy

Every demo has `deploy.sh` and `undeploy.sh` at the demo root.

- Both source `.env` and use parameterized variables
- `undeploy.sh` cleans up **all** resources created by `deploy.sh` (GCS buckets, agents, Cloud Run services, etc.)
- **Idempotency**: `deploy.sh` → `undeploy.sh` → `deploy.sh` must succeed without leftover resources
- Use `|| echo "already exists"` / `|| true` for idempotent resource creation/deletion
- `deployment_metadata.json` is the state artifact — `deploy.sh` writes it, `undeploy.sh` reads and removes it

### Rule #7 — Use AI Skills

Leverage the installed AI skills whenever working on this repo.

- `google-agents-cli-*` skills for all ADK development (scaffold, code, deploy, eval, observe, publish)
- `google-dev-knowledge` MCP server for Google Cloud documentation (preferred over WebFetch/WebSearch)
- `cloud-run-basics`, `bigquery-basics`, `firebase-basics`, `gke-basics` etc. when touching those services
- Check ADK source and samples before implementing new patterns

### Rule #8 — Documentation

Every demo must have two documentation files:

- **`README.md`** — Quick start, prerequisites, configuration table, key learnings
- **`ARCHITECTURE.md`** — Heavily detailed, ASCII art rich, with:
  - Architecture diagrams showing all components and their interactions
  - Sequence diagrams for key flows
  - Code snippets explaining integration points
  - Architecture decisions and rationale
  - The goal is to **teach** the reader, not just document

Once a demo is completed and standardized, it must be indexed in the root `README.md`.

### Rule #9 — Demo Guide

Every demo must have a `DEMO.md` that walks a user through demonstrating the functionality step by step. This is the "show script" — what to run, what to say, what to observe.

Structure:
- **Prerequisites** — what must be deployed, any setup steps
- **Access methods** — how to interact with the agent (list all available options):
  - `agents-cli run --url <url> --mode adk "prompt"` (from local terminal)
  - Console Playground (link with agent engine ID)
  - Demo scripts (`uv run python ../scripts/<script>.py`)
- **Demo scenarios** — numbered acts/sections, each with:
  - Context: what this scenario demonstrates
  - Sample prompts: exact copy-pasteable commands
  - What to observe: expected agent behavior, which tools fire, what data appears
- **Verification** — how to confirm things worked (traces, dashboard, logs)
- **Cleanup** — how to reset demo state without full undeploy (e.g., clear sessions/memories)

Guidelines:
- Prompts and agent instructions can be in English or Portuguese-BR depending on the demo's target audience
- Use `agents-cli run` as the primary interaction method (works from any terminal)
- Include Console Playground links as an alternative for visual demos
- Cover happy path, edge cases, and the "wow" moments that make the demo compelling
- Keep each scenario focused — one concept per act

### Rule #10 — Optional Gemini Enterprise Registration

Agents can optionally be registered with a Gemini Enterprise (GE) App. This is controlled entirely by env vars — if they're not set, registration is silently skipped.

- **Trigger env var**: `GEMINI_ENTERPRISE_APP_ID` — if empty or unset, skip registration
- **Additional vars**: `GEMINI_DISPLAY_NAME`, `GEMINI_DESCRIPTION` (defaults derived from `AGENT_DISPLAY_NAME`)
- A demo folder may contain multiple agents — each can have its own GE registration vars (e.g., `GEMINI_DISPLAY_NAME_1`, or by running publish per agent sequentially)

**In `deploy.sh`** — after the agent deploy step succeeds:
```bash
if [ -n "${GEMINI_ENTERPRISE_APP_ID:-}" ]; then
    echo ">>> Registering with Gemini Enterprise..."
    agents-cli publish gemini-enterprise \
        --gemini-enterprise-app-id "${GEMINI_ENTERPRISE_APP_ID}" \
        --display-name "${GEMINI_DISPLAY_NAME:-${AGENT_DISPLAY_NAME}}" \
        --description "${GEMINI_DESCRIPTION:-Agent deployed from $(basename $(pwd))}" \
        --no-confirm-project
fi
```

**In `undeploy.sh`** — before deleting the agent, deregister from GE if the app ID and agent ID are known:
```bash
if [ -n "${GEMINI_ENTERPRISE_APP_ID:-}" ] && [ -n "${GE_AGENT_ID:-}" ]; then
    echo ">>> Deregistering from Gemini Enterprise..."
    curl -s -X DELETE \
        "https://global-discoveryengine.googleapis.com/v1alpha/${GEMINI_ENTERPRISE_APP_ID}/assistants/default_assistant/agents/${GE_AGENT_ID}" \
        -H "Authorization: Bearer $(gcloud auth print-access-token)" || echo "    GE agent not found."
fi
```

**In `.env.template`** — add as optional block:
```bash
# === Gemini Enterprise Registration (optional) ===
# Set GEMINI_ENTERPRISE_APP_ID to register this agent with a GE App after deploy.
# Format: projects/<project>/locations/global/collections/default_collection/engines/<app-id>
# GEMINI_ENTERPRISE_APP_ID=
# GEMINI_DISPLAY_NAME=
# GEMINI_DESCRIPTION=
```

### Rule #11 — Unique Agent Names

Every agent in the repo must have a globally unique name — across all demos — to avoid collisions when multiple demos are deployed to the same GCP project simultaneously.

Three identifiers must be unique per agent:

1. **`root_agent.name`** in `agent.py` — this becomes the display name in Agent Registry for A2A agents and the agent label in `agents-cli run` output
2. **`[tool.agents-cli] name`** in `pyproject.toml` — this is the display name in Agent Runtime (Reasoning Engine) and what `agents-cli deploy` uses to find/update existing agents
3. **`[project] name`** in `pyproject.toml` — the Python package name; should also be unique to avoid confusion

**Naming convention:** prefix agent names with the demo name or a short unique identifier:
```
# a2a-demo
root_agent = Agent(name="currency_specialist", ...)     # in agent.py
[tool.agents-cli] name = "specialist-agent"             # in pyproject.toml

# spiffe-registry-demo
root_agent = Agent(name="spiffe_currency_specialist", ...)
[tool.agents-cli] name = "spiffe-specialist"
```

**Why this matters:**
- `agents-cli deploy` matches by `[tool.agents-cli] name` — if two demos share the same name, deploying one overwrites the other
- Agent Registry indexes by `root_agent.name` for A2A agents — duplicate names cause ambiguous discovery
- Undeploy in one demo can accidentally delete another demo's agent

---

## Project Structure

```
ge-agentplatform-demos/
├── CLAUDE.md                     # These development standards
├── LEARNINGS.md                  # Hard-won implementation knowledge
├── README.md                     # Demo index and quick start
├── .gitignore
├── skills-lock.json
├── sessions-memory-demo/         # Active demo: Sessions + Memory Bank
├── evals-demo/                   # Active demo: Online evaluation
└── experimental/                 # Non-conforming demos and references
    ├── README.md
    ├── _template/
    ├── governance-demo/
    └── test-agent-gateway-codelab/
```

Each active demo follows this layout:
```
<demo-name>/
├── .env.template                 # All env vars with defaults and docs
├── deploy.sh                     # Idempotent deployment
├── undeploy.sh                   # Full resource cleanup
├── README.md                     # Quick start guide
├── ARCHITECTURE.md               # Detailed architecture documentation
├── DEMO.md                       # Step-by-step demo script with sample prompts
└── demo-agent/
    ├── pyproject.toml             # Dependencies with version ranges
    └── app/
        ├── __init__.py
        ├── agent.py               # Agent definition
        └── agent_runtime_app.py   # Agent Runtime entrypoint
```

---

## Creating a New Demo

1. `agents-cli scaffold create <demo-name>` (in repo root)
2. `agents-cli scaffold enhance` to add deployment target, CI/CD if needed
3. Add `.env.template` with all parameterized variables (Rule #1)
4. Create `deploy.sh` / `undeploy.sh` wrappers — `deploy.sh` calls `agents-cli deploy` with `--update-env-vars` for telemetry (Rules #4, #6)
5. Test locally first: `agents-cli run "test prompt"` (Rule #3)
6. Deploy and verify: `./deploy.sh`
7. Write `README.md` and `ARCHITECTURE.md` (Rule #8)
8. Update root `README.md` demo index

---

## Agent Identity and IAM

By default, agents use the standard service account approach for Agent Runtime — no special identity config needed.

If a demo specifically requires agent-level identity (e.g., cross-project access, Agent Gateway governance), SPIFFE identity (`IdentityType.AGENT_IDENTITY`) and principal sets are available. Document the need in that demo's `ARCHITECTURE.md` and use `experimental/setup-project.sh` for principal set IAM grants.

---

## Telemetry Reference

For `agents-cli deploy` (preferred):
```bash
agents-cli deploy \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --update-env-vars "GEMINI_MODEL=${GEMINI_MODEL},GOOGLE_CLOUD_LOCATION=global,LOGS_BUCKET_NAME=${STAGING_BUCKET},OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY,OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false"
```

For `deploy_agent.py` fallback:
```python
env_vars = {
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "EVENT_ONLY",
    "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
    "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "false",
    "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true",
    "LOGS_BUCKET_NAME": f"{project_id}-<demo>-staging",
}
```

In `telemetry.py`, always use `os.environ.setdefault()` so deploy-time values take precedence.

---

## External References

- **ADK source code** — <https://github.com/google/adk-python>
- **ADK sample agents** — <https://github.com/google/adk-samples/tree/main/python/agents>
- **Google Cloud docs** — use `google-dev-knowledge` MCP server (`search_documents`, `get_documents`, `answer_query`)
