# Evals Demo — Architecture

This document explains the architecture, data flows, and design decisions behind the Online Monitoring demo for Google Cloud's Agent Platform.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Google Cloud Project                              │
│                                                                             │
│  ┌───────────┐      ┌────────────────────────────────────────────────────┐  │
│  │           │      │              Agent Runtime                         │  │
│  │  User /   │─────▶│                                                    │  │
│  │  CLI /    │      │  ┌──────────────────┐    ┌──────────────────────┐  │  │
│  │  Script   │      │  │  ADK Agent       │    │  OpenTelemetry       │  │  │
│  │           │◀─────│  │  (support_agent) │───▶│  Instrumentation     │  │  │
│  └───────────┘      │  │                  │    │                      │  │  │
│                     │  │  Tools:          │    │  - Cloud Trace spans │  │  │
│                     │  │  - lookup_order  │    │  - GenAI events      │  │  │
│                     │  │  - search_faq    │    │  - GCS log uploads   │  │  │
│                     │  │  - create_ticket │    └──────────┬───────────┘  │  │
│                     │  └──────────────────┘               │              │  │
│                     └────────────────────────────────────┬┘              │  │
│                                                          │               │  │
│                     ┌────────────────────────────────────▼──────────┐    │  │
│                     │              Cloud Trace                      │    │  │
│                     │                                               │    │  │
│                     │  Stores spans with GenAI semantic convention: │    │  │
│                     │  - gen_ai.content.prompt                      │    │  │
│                     │  - gen_ai.content.completion                  │    │  │
│                     │  - gen_ai.tool.definitions                    │    │  │
│                     │  - gen_ai.tool.calls / gen_ai.tool.results    │    │  │
│                     └────────────────────────────────────┬──────────┘    │  │
│                                                          │               │  │
│                     ┌────────────────────────────────────▼──────────┐    │  │
│                     │         Gen AI Evaluation Service              │    │  │
│                     │         (Online Monitor)                       │    │  │
│                     │                                               │    │  │
│                     │  ┌─────────────────────────────────────────┐  │    │  │
│                     │  │  Sampling: N% of traces                 │  │    │  │
│                     │  │                                         │  │    │  │
│                     │  │  LLM Judge evaluates each sample:       │  │    │  │
│                     │  │  ┌─────────────────────────────────┐   │  │    │  │
│                     │  │  │ FINAL_RESPONSE_QUALITY  (1-5)   │   │  │    │  │
│                     │  │  │ TOOL_USE_QUALITY        (1-5)   │   │  │    │  │
│                     │  │  │ HALLUCINATION           (0/1)   │   │  │    │  │
│                     │  │  │ SAFETY                  (0/1)   │   │  │    │  │
│                     │  │  └─────────────────────────────────┘   │  │    │  │
│                     │  └─────────────────────────────────────────┘  │    │  │
│                     │                                               │    │  │
│                     │  Results → Console Dashboard > Evaluation     │    │  │
│                     └───────────────────────────────────────────────┘    │  │
│                                                                          │  │
│  ┌──────────────────────────────────────────────────────────────────┐    │  │
│  │  GCS Staging Bucket                                              │    │  │
│  │  gs://<project-id>-evals-staging/                                │    │  │
│  │                                                                  │    │  │
│  │  /completions/  ← JSONL logs of prompts + completions            │    │  │
│  │  /staging/      ← Agent deployment artifacts                     │    │  │
│  └──────────────────────────────────────────────────────────────────┘    │  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Request Flow — Sequence Diagram

```
User                 Agent Runtime          ADK Agent           Cloud Trace         Online Monitor
 │                        │                     │                    │                    │
 │  POST /query           │                     │                    │                    │
 │───────────────────────▶│                     │                    │                    │
 │                        │  invoke agent       │                    │                    │
 │                        │────────────────────▶│                    │                    │
 │                        │                     │                    │                    │
 │                        │                     │  call_llm          │                    │
 │                        │                     │  (Gemini API)      │                    │
 │                        │                     │◀──────────────────▶│                    │
 │                        │                     │                    │                    │
 │                        │                     │  execute_tool      │                    │
 │                        │                     │  (lookup_order)    │                    │
 │                        │                     │                    │                    │
 │                        │                     │  call_llm          │                    │
 │                        │                     │  (final response)  │                    │
 │                        │                     │                    │                    │
 │                        │                     │  emit GenAI events │                    │
 │                        │                     │───────────────────▶│                    │
 │                        │                     │                    │                    │
 │  response              │                     │                    │                    │
 │◀───────────────────────│                     │                    │                    │
 │                        │                     │                    │                    │
 │                        │                     │                    │  sample trace      │
 │                        │                     │                    │───────────────────▶│
 │                        │                     │                    │                    │
 │                        │                     │                    │  LLM judge scores  │
 │                        │                     │                    │◀───────────────────│
 │                        │                     │                    │                    │
 │                        │                     │                    │  store results     │
 │                        │                     │                    │  → Dashboard       │
```

---

## Trace Structure

The ADK OpenTelemetry instrumentation produces a span tree for each invocation:

```
invocation (root span)
├── agent_run: support_agent
│   ├── call_llm: gemini-3-flash-preview
│   │   ├── GenAI Event: gen_ai.content.prompt       ← user message
│   │   ├── GenAI Event: gen_ai.tool.definitions     ← tool schemas
│   │   └── GenAI Event: gen_ai.content.completion    ← model response (tool call)
│   │
│   ├── execute_tool: lookup_order
│   │   └── GenAI Event: gen_ai.tool.results          ← tool output
│   │
│   └── call_llm: gemini-3-flash-preview
│       ├── GenAI Event: gen_ai.content.prompt        ← tool result + context
│       └── GenAI Event: gen_ai.content.completion     ← final response to user
```

The Online Monitor reads these GenAI events to reconstruct the full conversation for LLM judge evaluation.

---

## Telemetry Configuration

### Why EVENT_ONLY?

There are three capture modes for `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`:

| Mode | Behavior | Use Case |
|------|----------|----------|
| `NO_CONTENT` | No payloads in traces | Production with PII concerns |
| `EVENT_ONLY` | Payloads as GenAI events (not span attributes) | **Required for Online Monitors** |
| `true` | Payloads in both events and span attributes | Debugging only (bloats traces) |

The Online Monitor requires GenAI events on traces. `NO_CONTENT` produces empty traces the monitor cannot evaluate. `true` is redundant — events are sufficient.

### Env Var Flow

```
deploy.sh
  │
  │  agents-cli deploy --update-env-vars "...EVENT_ONLY..."
  │
  ▼
Agent Runtime Container
  │
  │  OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY
  │  OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
  │  ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false
  │  GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true
  │  LOGS_BUCKET_NAME=<project>-evals-staging
  │
  ▼
telemetry.py (app startup)
  │
  │  os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", ...)
  │  ← setdefault does NOT override deploy-time value
  │
  │  If LOGS_BUCKET_NAME set and capture enabled:
  │    → OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT=jsonl
  │    → OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK=upload
  │    → OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH=gs://<bucket>/completions
  │
  ▼
OpenTelemetry SDK
  │
  │  Emits spans to Cloud Trace
  │  Uploads JSONL logs to GCS
```

### GOOGLE_CLOUD_LOCATION=global

Gemini 3.x models are only available via the global endpoint. `GOOGLE_CLOUD_LOCATION=global` routes LLM calls to `global-aiplatform.googleapis.com`. The Agent Runtime itself runs in `REGION` (e.g., `us-central1`), but the model inference call goes to global.

---

## Evaluation Metrics

The Online Monitor uses an LLM judge (Gemini) to evaluate each sampled trace:

### FINAL_RESPONSE_QUALITY (1-5)
Evaluates the final response the agent gave to the user. Considers accuracy, relevance, completeness, and helpfulness.

### TOOL_USE_QUALITY (1-5)
Evaluates whether the agent used the right tools with the right arguments. Checks:
- Did the agent call the correct tool for the user's request?
- Were the arguments passed correctly?
- Did the agent use the tool results appropriately?

### HALLUCINATION (0 or 1)
Binary check: did the agent fabricate information not present in tool results or context?

### SAFETY (0 or 1)
Binary check: did the agent produce harmful, biased, or inappropriate content?

---

## Deployment Architecture

### deploy.sh flow

```
deploy.sh
  │
  ├── Step 1: Create GCS staging bucket
  │   └── gcloud storage buckets create gs://<project>-evals-staging
  │       └── Idempotent: || echo "already exists"
  │
  └── Step 2: Deploy via agents-cli
      └── agents-cli deploy
          --project <project>
          --region <region>
          --update-env-vars <telemetry-vars>
          --no-confirm-project
```

### undeploy.sh flow

```
undeploy.sh
  │
  ├── Step 1: Delete agent from Agent Runtime
  │   ├── Read deployment_metadata.json for resource name
  │   ├── Extract reasoning engine ID
  │   └── DELETE via Vertex AI REST API (force=true)
  │
  └── Step 2: Delete GCS staging bucket
      └── gcloud storage rm --recursive gs://<bucket>
```

### Idempotency guarantees

| Operation | Idempotent? | Mechanism |
|-----------|-------------|-----------|
| Bucket creation | Yes | `|| echo "already exists"` |
| Agent deploy | Yes | agents-cli handles create-or-update |
| Agent delete | Yes | `|| echo "Agent not found"` |
| Bucket delete | Yes | `|| echo "Bucket not found"` |
| Second deploy after undeploy | Yes | Creates fresh resources |

---

## Design Decisions

### Why agents-cli deploy (not deploy_agent.py)?

This demo has no features that require the `vertexai.Client` fallback — no `context_spec`, no `agentGatewayConfig`. It uses the standard ADK agent pattern, so `agents-cli deploy` is the correct choice per Rule #2.

### Why mock tools (not real backends)?

The demo's purpose is to show the evaluation pipeline, not the agent's business logic. Mock tools return deterministic data that makes evaluation results predictable and reproducible.

### Why no custom telemetry.py?

When using `agents-cli deploy`, the scaffolded `telemetry.py` is included automatically. The deploy-time env vars (`--update-env-vars`) control the behavior. No custom telemetry code is needed.

---

## Setting Up the Online Monitor

The Online Monitor is configured via the Console (no API/CLI yet):

```
Console > Agent Platform > Agents > Deployments
  │
  └── Select deployed agent
      │
      └── Dashboard > Evaluation > New Monitor
          │
          ├── Select metrics:
          │   ├── FINAL_RESPONSE_QUALITY
          │   ├── TOOL_USE_QUALITY
          │   ├── HALLUCINATION
          │   └── SAFETY
          │
          ├── Sampling rate: 100% (for demo; lower in production)
          │
          └── Create
```

After creation, every new trace that matches the sampling rate is automatically evaluated. Results appear in the Evaluation dashboard within 1-2 minutes.

---

## Traffic Generation

The `scripts/generate_traffic.py` script sends a batch of diverse queries to the deployed agent to produce traces for the monitor to evaluate:

```bash
cd demo-agent
uv run python ../scripts/generate_traffic.py
```

Query categories:
- **Order lookups**: tests `lookup_order` tool usage
- **FAQ searches**: tests `search_faq` tool usage
- **Escalations**: tests `create_ticket` tool usage
- **Edge cases**: unknown orders, ambiguous queries
