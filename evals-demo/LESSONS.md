# Agent Evaluation on Agent Platform — A Tutorial

A hands-on guide to evaluating deployed agents with online monitors. Written from lessons learned building and deploying this demo to Google Agent Platform (May 2026).

---

## 1. The Problem: "Is My Agent Actually Good?"

You've built an agent, tested it locally, deployed it. Users are chatting with it. But how do you know it's performing well? Is it hallucinating? Are tool calls correct? Is it safe?

```
                    The Observability Gap

    Development                            Production
    ──────────                             ──────────
    agents-cli eval run                    ???
    ├── Tool trajectory ✓                  ├── Response quality ???
    ├── Response quality ✓                 ├── Hallucinations ???
    └── You see every case                 └── 1000s of conversations
                                               you'll never read
```

ADK's local eval (`agents-cli eval run`) is great for development — but it runs on your machine, against your evalset. In production, you need **continuous, automated evaluation** of live traffic. That's what Agent Platform's **Online Monitors** do.

---

## 2. Online Monitors — Continuous Production Evaluation

An online monitor watches your deployed agent's traces and automatically evaluates them with LLM-as-judge metrics. It runs server-side — no scripts, no cron jobs, no infrastructure to manage.

```
    ┌──────────────────────────────────────────────────────────┐
    │                  Agent Runtime                           │
    │                                                          │
    │  ┌──────────────┐    ┌────────────────────────────────┐ │
    │  │  Your Agent   │───▶│  Cloud Trace (with GenAI events)│ │
    │  │  (ADK)        │    │                                │ │
    │  └──────────────┘    │  gen_ai.input.messages_ref     │ │
    │                       │  gen_ai.output.messages_ref    │ │
    │                       │  gen_ai.system_instructions    │ │
    │                       │  gen_ai.tool.definitions       │ │
    │                       └──────────────┬─────────────────┘ │
    └──────────────────────────────────────┼───────────────────┘
                                           │
                                           ▼
    ┌──────────────────────────────────────────────────────────┐
    │              Online Monitor (Gen AI Eval Service)         │
    │                                                           │
    │  Periodically samples traces and evaluates them:          │
    │                                                           │
    │  ┌─────────────────────────────────────────────────────┐ │
    │  │  FINAL_RESPONSE_QUALITY  — Did the agent answer well?│ │
    │  │  TOOL_USE_QUALITY        — Were tool calls correct?  │ │
    │  │  HALLUCINATION           — Did it make things up?    │ │
    │  │  SAFETY                  — Was it harmful?           │ │
    │  └─────────────────────────────────────────────────────┘ │
    │                                                           │
    │  Results visible in:                                      │
    │  Console > Agent Platform > Agents > Dashboard > Eval     │
    └──────────────────────────────────────────────────────────┘
```

### What the monitor evaluates

Each metric uses **adaptive rubrics** — the evaluation service auto-generates per-prompt pass/fail tests, then uses an LLM judge to score them. This is more nuanced than static thresholds.

| Metric | What it checks | Example rubric |
|--------|----------------|----------------|
| `FINAL_RESPONSE_QUALITY` | Did the response address the user's request? | "The answer states the correct order status" |
| `TOOL_USE_QUALITY` | Were the right tools called with correct arguments? | "The agent calls lookup_order with the provided order ID" |
| `HALLUCINATION` | Is the response grounded in tool outputs and instructions? | "No claims not supported by the tool response" |
| `SAFETY` | Is the response free from harmful content? | Standard safety evaluation |

---

## 3. The Telemetry Requirement

Online monitors don't evaluate your agent code directly — they evaluate **traces**. For this to work, traces must include GenAI events with input/output data. Without proper telemetry, the monitor sees traces but has no content to evaluate.

```
    Trace WITHOUT GenAI events            Trace WITH GenAI events
    (monitor can't evaluate)              (monitor can evaluate)

    ┌─────────────────────┐              ┌─────────────────────┐
    │ invoke_agent        │              │ invoke_agent        │
    │  └─ generate_content│              │  └─ generate_content│
    │     duration: 1.2s  │              │     duration: 1.2s  │
    │     model: gemini-3 │              │     model: gemini-3 │
    │     tokens: 215/22  │              │     tokens: 215/22  │
    │                     │              │     ┌───────────────┤
    │     (no events)     │              │     │ INPUT:        │
    │                     │              │     │  gs://bucket/ │
    │                     │              │     │  ..._inputs   │
    └─────────────────────┘              │     │ OUTPUT:       │
                                         │     │  gs://bucket/ │
    Console shows:                       │     │  ..._outputs  │
    "No events that match                │     │ TOOLS:        │
     OpenTelemetry conventions           │     │  lookup_order │
     for genAI data to display."         │     │  search_faq   │
                                         │     │  create_ticket│
                                         │     └───────────────┤
                                         └─────────────────────┘

                                         Console shows:
                                         Input/Output tab with
                                         full prompts & responses
```

### The three environment variables that make it work

These must be set at deploy time as env vars on the Agent Runtime:

```python
# 1. Capture mode — attach messages as events on trace spans
"OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "EVENT_ONLY"

# 2. Semantic convention — use the latest GenAI event format
"OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental"

# 3. Prevent PII leaking into span attributes (content goes to GCS instead)
"ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "false"
```

Plus `LOGS_BUCKET_NAME` — a GCS bucket where prompts/responses are uploaded as JSONL. The traces reference these files via `_ref` attributes (e.g., `gen_ai.input.messages_ref`).

### What goes wrong without these

| Missing variable | What happens |
|------------------|--------------|
| `CAPTURE_MESSAGE_CONTENT` not set or `"true"` | Warning: "true is not a valid option". Defaults to `NO_CONTENT`. No events on spans. |
| `OTEL_SEMCONV_STABILITY_OPT_IN` not set | Events use old format. Console doesn't recognize them. |
| `LOGS_BUCKET_NAME` not set | Upload hook has no destination. Content capture disabled. |
| `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS` not `"false"` | PII in span attributes, may exceed attribute size limits. |

---

## 4. What We Changed from `agents-cli` Defaults

The standard `agents-cli scaffold create` + `agents-cli deploy` flow gets you 90% of the way. We made exactly **two changes** to make online monitors work:

### Change 1: Deploy-time env vars via `--update-env-vars`

`agents-cli deploy` sets `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` by default, which is an invalid value. We override it in `deploy.sh`:

```bash
agents-cli deploy \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --update-env-vars "\
GEMINI_MODEL=${GEMINI_MODEL},\
GOOGLE_CLOUD_LOCATION=global,\
LOGS_BUCKET_NAME=${STAGING_BUCKET},\
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=EVENT_ONLY,\
OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,\
ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false" \
    --no-confirm-project
```

### Change 2: Fix `telemetry.py` — remove the hard-override

The scaffolded `telemetry.py` has this line that **overwrites** whatever you set at deploy time:

```python
# BEFORE (scaffolded default) — BAD
os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "NO_CONTENT"
```

We changed it to respect the deploy-time value:

```python
# AFTER (our fix) — GOOD
mode = os.environ.get("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "EVENT_ONLY")
logging.info("Prompt-response logging enabled - mode: %s", mode)
```

That's it. Everything else is standard `agents-cli` scaffolding.

---

## 5. The Agent — Keep It Simple

This demo uses a customer support agent with three mock tools. The agent itself is intentionally trivial — the focus is on the evaluation infrastructure, not agent complexity.

```python
# app/agent.py

def lookup_order(order_id: str) -> dict:
    """Looks up the status and details of a customer order by its ID."""
    orders = {
        "ORD-123": {"status": "shipped", "items": ["Wireless Mouse", "USB-C Hub"], ...},
        "ORD-456": {"status": "delivered", "items": ["Mechanical Keyboard"], ...},
        "ORD-789": {"status": "processing", "items": ["Monitor Stand", "Webcam"], ...},
    }
    return orders.get(order_id, {"error": f"Order {order_id} not found."})

def search_faq(query: str) -> dict:
    """Searches the FAQ knowledge base for answers matching the query."""
    ...

def create_ticket(subject: str, description: str) -> dict:
    """Creates a support ticket for issues that need human follow-up."""
    ...

root_agent = Agent(
    name="support_agent",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    instruction="You are a customer support assistant...",
    tools=[lookup_order, search_faq, create_ticket],
)

app = App(root_agent=root_agent, name="app")
```

The tools return deterministic mock data — perfect for evaluating whether the agent routes correctly:

| User query | Expected tool | Why it's evaluable |
|---|---|---|
| "Status of ORD-123?" | `lookup_order` | Clear tool match, verifiable response |
| "How do I reset my password?" | `search_faq` | FAQ lookup, answer should match |
| "My order arrived damaged" | `lookup_order` → `create_ticket` | Multi-step trajectory |

---

## 6. Setting Up an Online Monitor (Console)

After deploying the agent and sending a few queries:

### Step 1: Navigate to the Evaluation page

**Console > Agent Platform > Agents > Deployments > [your agent] > Dashboard > Evaluation**

### Step 2: Create a new online monitor

Click **New Monitor** and configure:

| Setting | Recommended value |
|---|---|
| Agent engine | Select your deployed agent |
| Filter criteria | All traces (or filter by duration, token usage) |
| Metrics | FINAL_RESPONSE_QUALITY, TOOL_USE_QUALITY, HALLUCINATION, SAFETY |
| Sampling percentage | 100% (for demo; lower in production) |
| Max samples per run | 50 (default) |

### Step 3: Send traffic and wait

The monitor polls periodically. Send several queries via playground or `agents-cli run`, then check back. Results appear in:

- **Dashboard > Evaluation** — time-series charts of metric scores
- **Individual traces** — click any trace, select the Evaluation tab to see per-trace scores and rubric verdicts

---

## 7. Offline Evaluation (Console)

For one-time assessments of historical traces/sessions:

### Step 1: Navigate to Evaluation

**Console > Agent Platform > Agents > Evaluation** (top-level, not per-agent)

### Step 2: Create evaluation

1. Click **New Evaluation**
2. Select **Traces** or **Sessions** tab
3. Filter by time range, version, or other criteria
4. Select the traces/sessions you want to evaluate
5. Click **Continue**
6. Choose metrics and provide a GCS output path
7. Click **Evaluate Agent**

### Step 3: View results

Results appear in the Evaluations list. Click an evaluation name to see:
- Summary metrics (mean scores)
- Per-trace breakdown with scores and rubric verdicts
- Click any row to drill into the associated trace

---

## 8. Deployment Flow

```
    ┌────────────────────────────────────────────────┐
    │              deploy.sh                          │
    │                                                 │
    │  1. Create GCS staging bucket                   │
    │     gs://{PROJECT_ID}-evals-staging              │
    │                                                 │
    │  2. agents-cli deploy                           │
    │     --update-env-vars:                          │
    │       GEMINI_MODEL=gemini-3-flash-preview       │
    │       GOOGLE_CLOUD_LOCATION=global     ◄────────┼── Gemini 3 needs global
    │       LOGS_BUCKET_NAME=...-evals-staging ◄──────┼── For GCS content upload
    │       CAPTURE_MESSAGE_CONTENT=EVENT_ONLY ◄──────┼── Attach events to spans
    │       SEMCONV_STABILITY=gen_ai_latest_exp ◄─────┼── Latest event format
    │       ADK_CAPTURE_IN_SPANS=false         ◄──────┼── Prevent PII in spans
    │                                                 │
    └─────────────────────┬───────────────────────────┘
                          │
                          ▼
    ┌────────────────────────────────────────────────┐
    │            Agent Runtime                        │
    │                                                 │
    │  Agent receives queries, generates traces       │
    │  with GenAI events (input/output refs to GCS)   │
    │                                                 │
    │  Online monitor evaluates traces continuously   │
    │  Results visible in Console > Dashboard > Eval  │
    └────────────────────────────────────────────────┘
```

---

## 9. Gotchas and Validated Findings

### `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` is invalid

The OpenTelemetry GenAI instrumentation changed from boolean to enum values. `"true"` silently defaults to `NO_CONTENT`, which means no content is captured. Valid values: `NO_CONTENT`, `SPAN_ONLY`, `EVENT_ONLY`, `SPAN_AND_EVENT`. Use `EVENT_ONLY` per the [ADK instrumentation docs](https://docs.cloud.google.com/stackdriver/docs/instrumentation/ai-agent-adk).

### The scaffolded `telemetry.py` hard-overrides to `NO_CONTENT`

Line 31 of the scaffolded `telemetry.py` does `os.environ["..."] = "NO_CONTENT"` — this overwrites whatever you set via `--update-env-vars`. You must change this to `setdefault()` or a read-only pattern.

### `GOOGLE_CLOUD_LOCATION=global` is required for Gemini 3 models

Agent Runtime runs in `us-central1` but Gemini 3 preview models require inference via the `global` endpoint. Without this env var, you get `404 NOT_FOUND: Publisher Model was not found`.

### `gcsfs` is required for GCS content upload

The completion hook (`OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK=upload`) uses `fsspec` to write to `gs://` paths, which requires the `gcsfs` package. The agents-cli scaffold includes it by default. If you're using a custom `pyproject.toml`, add `gcsfs>=2024.11.0`.

### `App()` wrapper is required, not raw `Agent`

The `agent_runtime_app.py` expects `app` to be an `App` instance (has `plugins` attribute). Exporting a raw `Agent` causes `'LlmAgent' object has no attribute 'plugins'` at startup.

```python
# WRONG — crashes on Agent Runtime
app = root_agent

# CORRECT
app = App(root_agent=root_agent, name="app")
```

### Online monitors need traces with GenAI events

The monitor message "No online monitor configured, or no matching traces found to evaluate" means the traces exist but lack GenAI event attributes. Fix the telemetry configuration (Section 3) and redeploy.

### Judge model rate limits (429)

The evaluation service uses an LLM judge to score each prompt/metric. With many prompts and metrics, you'll hit rate limits. The SDK retries automatically (up to 5 times with exponential backoff), but some evaluations may still fail on high-volume runs.

---

## 10. File Map

```
evals-demo/
│
├── deploy.sh                    # agents-cli deploy with telemetry env vars
├── undeploy.sh                  # REST API delete
├── .env.template                # PROJECT_ID, REGION, GEMINI_MODEL
│
└── demo-agent/                  # agents-cli scaffolded project
    ├── pyproject.toml            # ADK + evaluation deps
    │
    └── app/
        ├── __init__.py           # Imports app from agent.py
        ├── agent.py              # 3 tools + Agent + App wrapper
        ├── agent_runtime_app.py  # AdkApp with feedback + artifacts
        └── app_utils/
            ├── telemetry.py      # Fixed: respects deploy-time EVENT_ONLY
            └── typing.py         # Feedback schema
```

---

## References

- [Evaluate your agents](https://docs.cloud.google.com/gemini-enterprise-agent-platform/optimize/evaluation/evaluate-agents) — Overview of evaluation types
- [Run offline evaluations](https://docs.cloud.google.com/gemini-enterprise-agent-platform/optimize/evaluation/evaluate-offline) — Console-based trace/session evaluation
- [Continuous evaluation with online monitors](https://docs.cloud.google.com/gemini-enterprise-agent-platform/optimize/evaluation/evaluate-online) — Online monitor setup
- [Instrument ADK applications](https://docs.cloud.google.com/stackdriver/docs/instrumentation/ai-agent-adk) — Required telemetry configuration
- [Gen AI Evaluation Service overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/evaluation-overview) — Metrics and adaptive rubrics
