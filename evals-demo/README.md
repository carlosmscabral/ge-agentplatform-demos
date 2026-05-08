# Google Agent Platform Evals Demo

Demonstrates **Online Monitoring** with the **Gen AI Evaluation Service** on Google Cloud's Agent Platform — continuously evaluating a deployed agent's quality, tool usage, hallucination, and safety.

For a deep-dive into how everything works, see [LESSONS.md](LESSONS.md).

## Architecture

```
┌──────────────┐     ┌──────────────────────────────────────────────┐
│  User / CLI  │────▶│             Agent Runtime                    │
└──────────────┘     │                                              │
                     │  ┌──────────────────┐  ┌──────────────────┐ │
                     │  │ ADK Agent        │  │ Cloud Trace      │ │
                     │  │ (support_agent)  │──│ + GenAI Events   │ │
                     │  │ - lookup_order   │  │ (EVENT_ONLY)     │ │
                     │  │ - search_faq     │  └────────┬─────────┘ │
                     │  │ - create_ticket  │           │           │
                     │  └──────────────────┘           │           │
                     └─────────────────────────────────┼───────────┘
                                                       │
                     ┌─────────────────────────────────▼───────────┐
                     │         Online Monitor                       │
                     │         (Gen AI Evaluation Service)          │
                     │                                              │
                     │  Samples traces, scores with LLM judge:     │
                     │  - FINAL_RESPONSE_QUALITY                   │
                     │  - TOOL_USE_QUALITY                         │
                     │  - HALLUCINATION                            │
                     │  - SAFETY                                   │
                     │                                              │
                     │  Results → Console Dashboard > Evaluation   │
                     └──────────────────────────────────────────────┘
```

1. **ADK Agent**: Customer support agent with 3 mock tools. Deployed to Agent Runtime via `agents-cli deploy`.
2. **Telemetry**: Traces include GenAI events (prompts, responses, tool definitions) uploaded to GCS. Required for evaluation to work.
3. **Online Monitor**: Console-configured monitor that continuously evaluates live traces with adaptive rubric metrics.

---

## Quick Start

### Deploy

```bash
cp .env.template .env        # Fill in PROJECT_ID
./deploy.sh                  # Creates bucket + deploys via agents-cli
```

### Send traffic

```bash
cd demo-agent
agents-cli run --url <agent-url> --mode adk "What is the status of order ORD-123?"
agents-cli run --url <agent-url> --mode adk "My order ORD-456 arrived damaged"
agents-cli run --url <agent-url> --mode adk "How do I reset my password?"
```

### Set up online monitor

1. Console > **Agent Platform > Agents > Deployments** > select your agent
2. **Dashboard > Evaluation** > **New Monitor**
3. Select metrics: FINAL_RESPONSE_QUALITY, TOOL_USE_QUALITY, HALLUCINATION, SAFETY
4. Set sampling to 100%, click **Create**
5. Send more queries — monitor evaluates them automatically

### Cleanup

```bash
./undeploy.sh
```

---

## Key Learnings

| # | Learning | Details |
|---|----------|---------|
| 1 | **`EVENT_ONLY` is required** | `agents-cli` sets `CAPTURE_MESSAGE_CONTENT=true` (invalid). Must override to `EVENT_ONLY` via `--update-env-vars` |
| 2 | **Scaffolded `telemetry.py` overrides to `NO_CONTENT`** | Hard-coded override must be removed so deploy-time env var is respected |
| 3 | **`GOOGLE_CLOUD_LOCATION=global`** | Gemini 3 models require the global endpoint. Agent Runtime runs in us-central1 |
| 4 | **`App()` wrapper required** | `app = App(root_agent=..., name="app")` — raw Agent export crashes Agent Runtime |
| 5 | **Monitors need GenAI events on traces** | Without proper telemetry, monitor reports "no matching traces found" |
| 6 | **Judge model rate limits** | Evaluation uses LLM judge — expect 429s on high-volume runs |

See [LESSONS.md](LESSONS.md) for the full tutorial with diagrams and code examples.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT_ID` | auto-detected | GCP project ID |
| `REGION` | `us-central1` | GCP region |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Gemini model |
