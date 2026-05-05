# Google Agent Platform Sessions & Memory Demo

Demonstrates **ADK Sessions** and **Memory Bank** on Google Cloud's Agent Platform — building agents that remember context across conversations.

For a deep-dive into how everything works, see [LESSONS.md](LESSONS.md).

## Architecture

```
┌──────────────┐     ┌──────────────────────────────────────────────┐
│  User / CLI  │────▶│             Agent Runtime                    │
└──────────────┘     │                                              │
                     │  ┌──────────────────┐  ┌──────────────────┐ │
                     │  │ VertexAi         │  │ Memory Bank      │ │
                     │  │ SessionService   │  │ (via context_spec│ │
                     │  │ (automatic)      │  │  at deploy time) │ │
                     │  └────────┬─────────┘  └────────┬─────────┘ │
                     │           │                     │           │
                     │  ┌────────┴─────────────────────┴─────────┐ │
                     │  │  ADK Agent (customer_support_agent)     │ │
                     │  │  - 4 FunctionTools                     │ │
                     │  │  - PreloadMemoryTool (memory recall)   │ │
                     │  │  - after_agent_callback (memory write) │ │
                     │  └────────────────────────────────────────┘ │
                     └──────────────────────────────────────────────┘
```

1. **ADK Agent**: Customer support agent for "Acme Cloud Services" with Python FunctionTools (no MCP server). Deployed to Agent Runtime with SPIFFE identity.
2. **VertexAiSessionService**: Managed persistent sessions — automatic on Agent Runtime. `user:` state persists across sessions.
3. **Memory Bank**: Extracts semantic memories from conversations via `after_agent_callback`. Loads them into new sessions via `PreloadMemoryTool`.

---

## Demo Story

### Scenario A: "Breaks" — No Persistence

Customer reports a billing issue. Agent looks up account, creates ticket, saves preferences. Customer returns in a new session — agent has **zero context**.

```bash
cd demo-agent && uv run python ../scripts/demo_stateless.py
```

### Scenario B: "Works" — Sessions + Memory Bank

Same conversation, but deployed to Agent Runtime with Memory Bank. Customer returns — agent greets them by name, recalls the ticket, knows their notification preference.

```bash
cd demo-agent && uv run python ../scripts/demo_stateful.py
```

---

## Quick Start

### Scenario A (Local)

```bash
cd demo-agent && agents-cli install && uv run python ../scripts/demo_stateless.py
```

### Scenario B (Deployed)

```bash
cp .env.template .env        # Fill in PROJECT_ID
./deploy.sh                  # Deploys with Memory Bank config
cd demo-agent && uv run python ../scripts/demo_stateful.py
```

### Cleanup

```bash
./undeploy.sh
```

---

## Key Learnings

| # | Learning | Details |
|---|----------|---------|
| 1 | **State scoping** | `user:` prefix persists across sessions, no prefix is session-only, `temp:` clears per turn |
| 2 | **Memory Bank needs `context_spec`** | Must pass `ReasoningEngineContextSpec(memory_bank_config=...)` at deploy time. `agents-cli deploy` doesn't support this — use `deploy_agent.py` |
| 3 | **Agent Runtime sessions are automatic** | `VertexAiSessionService` is wired automatically — no code config needed |
| 4 | **Agent must use `App()` wrapper** | `app = App(root_agent=..., name="app")` — raw `Agent` export won't work with Memory Bank |
| 5 | **Memory generation is async** | `after_agent_callback` triggers extraction; memories available after ~10-20s |
| 6 | **State interpolation is fragile** | `{user:key}` in instructions throws `KeyError` when key is absent. Use Memory Bank instead |

See [LESSONS.md](LESSONS.md) for the full tutorial with diagrams and code examples.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PROJECT_ID` | auto-detected | GCP project ID |
| `REGION` | `us-central1` | GCP region |
| `AGENT_DISPLAY_NAME` | `sessions-memory-demo` | Display name |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Gemini model |

## Known Limitations

See [GAPS.md](GAPS.md).
