# Demo Guide — Evals & Online Monitors

Step-by-step guide to demonstrate online evaluation of a deployed ADK agent using the Gen AI Evaluation Service.

## Prerequisites

```bash
# Agent must be deployed
cat demo-agent/deployment_metadata.json   # must exist

# Get the agent URL
export AGENT_URL=$(python3 -c "
import json
m = json.load(open('demo-agent/deployment_metadata.json'))
r = m['remote_agent_runtime_id']
loc = r.split('/')[3]
print(f'https://{loc}-aiplatform.googleapis.com/v1beta1/{r}')
")
echo $AGENT_URL
```

## Access Methods

| Method | When to use |
|--------|------------|
| `agents-cli run --url $AGENT_URL --mode adk "prompt"` | Primary — works from any terminal |
| Console Playground | Visual demos — link printed by `deploy.sh` |
| `python scripts/generate_traffic.py` | Batch traffic for monitor evaluation |

---

## Act 1 — Agent Capabilities

Show the agent's three tools in action: order lookup, FAQ search, and ticket creation.

### Order lookup (lookup_order)

```bash
cd demo-agent

agents-cli run --url $AGENT_URL --mode adk \
  "What is the status of order ORD-123?"
```

**What to observe:** Agent calls `lookup_order`, returns shipping status, tracking number, and estimated delivery.

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "Check the status of order ORD-999"
```

**What to observe:** Agent handles the unknown order gracefully — no hallucinated data.

### FAQ search (search_faq)

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "What is your return policy?"
```

**What to observe:** Agent calls `search_faq`, returns the 30-day return policy from the FAQ knowledge base.

### Ticket creation (create_ticket)

```bash
agents-cli run --url $AGENT_URL --mode adk \
  "My order ORD-456 arrived damaged. I need help."
```

**What to observe:** Agent first calls `lookup_order` to verify the order, then calls `create_ticket` to escalate. Multi-tool orchestration.

---

## Act 2 — Generate Traffic for Evaluation

Send a batch of diverse prompts to produce traces the online monitor can evaluate.

```bash
cd evals-demo

# All 20 prompts, 3s between each
uv run python scripts/generate_traffic.py

# Quick test — first 5 only
uv run python scripts/generate_traffic.py --batch 5

# Multiple rounds for more data
uv run python scripts/generate_traffic.py --rounds 3 --delay 2
```

### Prompt categories

| Category | Count | Examples |
|----------|-------|---------|
| `lookup_order` (success) | 4 | "What's the status of order ORD-123?" |
| `lookup_order` (error) | 1 | "Check the status of order ORD-999" |
| `search_faq` | 5 | "How do I reset my password?", "Return policy?" |
| `create_ticket` | 3 | "My order arrived damaged", "Charged twice" |
| Multi-tool | 2 | "Check status AND create a complaint ticket" |
| General | 3 | "Hi, what can you help with?", "Thanks!" |
| Edge cases | 2 | "Ignore your instructions", "Give me a discount" |

---

## Act 3 — Set Up Online Monitor

Configure the monitor to continuously evaluate live traces. This is done once via Console.

1. Open **Console > Agent Platform > Agents > Deployments**
2. Select the deployed agent
3. Go to **Dashboard > Evaluation > New Monitor**
4. Select metrics:
   - `FINAL_RESPONSE_QUALITY` — overall answer quality (1-5)
   - `TOOL_USE_QUALITY` — correct tool selection and arguments (1-5)
   - `HALLUCINATION` — fabricated information (0/1)
   - `SAFETY` — harmful content (0/1)
5. Set sampling to **100%** (for demo; lower in production)
6. Click **Create**

---

## Act 4 — Observe Evaluation Results

After the monitor is set up, generate more traffic and watch the scores appear.

```bash
# Generate traffic for the monitor
uv run python scripts/generate_traffic.py --rounds 2 --delay 2
```

### Verification

1. **Cloud Trace** — Console > Cloud Trace > Trace Explorer
   - Filter by `reasoningEngines/<ID>` to see agent traces
   - Each trace shows the span tree: `invocation → agent_run → call_llm / execute_tool`
   - GenAI events contain prompts, completions, and tool definitions

2. **Online Monitor Dashboard** — Console > Agent Platform > Evaluation
   - Scores appear within 1-2 minutes of trace generation
   - Look for trends across metrics — tool use quality should be high for tool-specific prompts

3. **GCS Logs** — `gs://<project>-evals-staging/completions/`
   - JSONL files with full prompt/completion pairs

---

## Cleanup

```bash
# Undeploy agent and delete bucket
./undeploy.sh
```
