# Sessions & Memory Demo — Architecture

This document explains the architecture, data flows, and design decisions behind the Sessions + Memory Bank demo for Google Cloud's Agent Platform.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Google Cloud Project                               │
│                                                                                 │
│  ┌───────────┐      ┌──────────────────────────────────────────────────────┐    │
│  │           │      │                 Agent Runtime                        │    │
│  │  User /   │─────▶│                                                      │    │
│  │  CLI /    │      │  ┌──────────────────────────────────────────────┐    │    │
│  │  Script   │◀─────│  │  ADK Agent (customer_support_agent)          │    │    │
│  │           │      │  │                                              │    │    │
│  └───────────┘      │  │  Tools:                                      │    │    │
│                     │  │  ├── lookup_account     (account data)       │    │    │
│                     │  │  ├── check_ticket_status (ticket lookup)     │    │    │
│                     │  │  ├── create_ticket       (ticket creation)   │    │    │
│                     │  │  ├── get_preferences     (read session state)│    │    │
│                     │  │  ├── update_preference   (write session state│    │    │
│                     │  │  └── PreloadMemoryTool   (memory recall)     │    │    │
│                     │  │                                              │    │    │
│                     │  │  Callback:                                    │    │    │
│                     │  │  └── after_agent_callback                    │    │    │
│                     │  │      └── add_session_to_memory()             │    │    │
│                     │  └──────────┬───────────────────┬───────────────┘    │    │
│                     │             │                   │                    │    │
│                     │  ┌──────────▼─────────┐  ┌─────▼────────────────┐  │    │
│                     │  │ VertexAi           │  │ Memory Bank          │  │    │
│                     │  │ SessionService     │  │                      │  │    │
│                     │  │                    │  │ Topics:              │  │    │
│                     │  │ Stores:            │  │ ├── KEY_CONVERSATION │  │    │
│                     │  │ ├── session state  │  │ │   _DETAILS         │  │    │
│                     │  │ │   (user: keys)   │  │ └── EXPLICIT_       │  │    │
│                     │  │ ├── conversation   │  │     INSTRUCTIONS     │  │    │
│                     │  │ │   events         │  │                      │  │    │
│                     │  │ └── temp state     │  │ Extracts semantic    │  │    │
│                     │  │                    │  │ memories from        │  │    │
│                     │  │ Managed by Agent   │  │ conversations        │  │    │
│                     │  │ Runtime (no code)  │  │                      │  │    │
│                     │  └────────────────────┘  └──────────────────────┘  │    │
│                     └────────────────────────────────────────────────────┘    │
│                                                                               │
│  ┌────────────────────────────────────────────────────────────────────────┐   │
│  │  GCS Staging Bucket  gs://<project-id>-sessions-demo-staging/          │   │
│  │  └── /completions/  ← JSONL telemetry logs                            │   │
│  └────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Ownership Model

The most important architectural decision in this demo is the clear separation of data ownership between Session State and Memory Bank:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Data Ownership                                   │
│                                                                     │
│  ┌──────────────────────────┐   ┌──────────────────────────────┐   │
│  │     SESSION STATE        │   │       MEMORY BANK            │   │
│  │     (Structured Data)    │   │       (Semantic Knowledge)   │   │
│  │                          │   │                              │   │
│  │  Owns:                   │   │  Owns:                       │   │
│  │  ├── preferred_name      │   │  ├── Past issue summaries    │   │
│  │  ├── customer_id         │   │  ├── Ticket outcomes         │   │
│  │  ├── notification_channel│   │  ├── Topics discussed        │   │
│  │  └── timezone            │   │  ├── User instructions       │   │
│  │                          │   │  └── Behavioral patterns     │   │
│  │  Access:                 │   │                              │   │
│  │  ├── get_preferences()   │   │  Access:                     │   │
│  │  └── update_preference() │   │  ├── PreloadMemoryTool       │   │
│  │                          │   │  │   (automatic at start)    │   │
│  │  Scope: user: prefix     │   │  └── after_agent_callback    │   │
│  │  Persists: across        │   │      (write after each       │   │
│  │  sessions for same user  │   │       conversation)          │   │
│  └──────────────────────────┘   └──────────────────────────────┘   │
│                                                                     │
│  KEY RULE: Never store structured preferences in Memory Bank.       │
│  Never rely on Memory Bank for data that session state already owns.│
└─────────────────────────────────────────────────────────────────────┘
```

### Why this separation?

- **Session state** is deterministic — `get_preferences()` always returns the exact value saved. Ideal for structured settings (name, timezone, notification channel).
- **Memory Bank** is semantic — it extracts insights from natural language conversations. It may paraphrase, summarize, or miss details. Ideal for contextual knowledge (past issues, behavioral patterns).

---

## State Scoping in ADK

```
┌─────────────────────────────────────────────────────────────────┐
│                   ADK State Key Prefixes                         │
│                                                                 │
│  Prefix         Lifetime              Example                   │
│  ─────────────  ────────────────────  ────────────────────────  │
│  user:          Cross-session         user:preferred_name       │
│                 (same user_id)        user:customer_id          │
│                                       user:notification_channel │
│                                                                 │
│  (no prefix)    Single session        current_ticket_id         │
│                 (cleared on new       conversation_topic        │
│                  session)                                       │
│                                                                 │
│  temp:          Single turn           temp:search_results       │
│                 (cleared each turn)   temp:intermediate_calc    │
└─────────────────────────────────────────────────────────────────┘
```

The `user:` prefix is critical — it tells `VertexAiSessionService` to persist the value across sessions for the same `user_id`. The `get_preferences` and `update_preference` tools use this prefix:

```python
# Writing (update_preference)
tool_context.state[f"user:{key}"] = value

# Reading (get_preferences)
prefs = {
    k.removeprefix("user:"): v
    for k, v in tool_context.state.to_dict().items()
    if k.startswith("user:")
}
```

---

## Memory Bank Flow — Sequence Diagrams

### Memory Write (after_agent_callback)

```
User             ADK Agent         VertexAiSessionService    Memory Bank
 │                   │                      │                     │
 │  "My name is     │                      │                     │
 │   Alex, billing  │                      │                     │
 │   issue..."      │                      │                     │
 │──────────────────▶│                      │                     │
 │                   │                      │                     │
 │                   │  update_preference   │                     │
 │                   │  (user:preferred_name│                     │
 │                   │   = "Alex")          │                     │
 │                   │─────────────────────▶│                     │
 │                   │                      │  store user: key    │
 │                   │                      │                     │
 │                   │  lookup_account,     │                     │
 │                   │  create_ticket, etc. │                     │
 │                   │                      │                     │
 │  response         │                      │                     │
 │◀──────────────────│                      │                     │
 │                   │                      │                     │
 │                   │  after_agent_callback │                     │
 │                   │  ────────────────────────────────────────▶ │
 │                   │  add_session_to_memory()                   │
 │                   │                      │                     │
 │                   │                      │  Memory Bank extracts│
 │                   │                      │  semantic insights:  │
 │                   │                      │  "Alex had a billing │
 │                   │                      │   issue with account │
 │                   │                      │   cust_001. Ticket   │
 │                   │                      │   TKT-001 created."  │
 │                   │                      │     (async, ~10-20s) │
```

### Memory Recall (PreloadMemoryTool — next session)

```
User             ADK Agent         VertexAiSessionService    Memory Bank
 │                   │                      │                     │
 │  "Hi, I had a    │                      │                     │
 │   billing issue  │                      │                     │
 │   last week"     │                      │                     │
 │──────────────────▶│                      │                     │
 │                   │                      │                     │
 │                   │  PreloadMemoryTool   │                     │
 │                   │  (automatic)         │                     │
 │                   │──────────────────────────────────────────▶│
 │                   │                      │                     │
 │                   │  Memories returned:  │                     │
 │                   │  "Alex had billing   │◀────────────────────│
 │                   │   issue, ticket      │                     │
 │                   │   TKT-001 resolved"  │                     │
 │                   │                      │                     │
 │                   │  get_preferences     │                     │
 │                   │─────────────────────▶│                     │
 │                   │  {preferred_name:    │                     │
 │                   │   "Alex", ...}       │                     │
 │                   │◀─────────────────────│                     │
 │                   │                      │                     │
 │  "Hi Alex! I see │                      │                     │
 │   your ticket    │                      │                     │
 │   TKT-001 was    │                      │                     │
 │   resolved..."   │                      │                     │
 │◀──────────────────│                      │                     │
```

---

## Memory Bank Configuration

Memory Bank is configured at deploy time via `context_spec` in `deploy_agent.py`:

```python
from vertexai._genai.types import (
    ManagedTopicEnum,
    MemoryBankCustomizationConfig as CustomizationConfig,
    MemoryBankCustomizationConfigMemoryTopic as MemoryTopic,
    MemoryBankCustomizationConfigMemoryTopicManagedMemoryTopic as ManagedMemoryTopic,
    ReasoningEngineContextSpecMemoryBankConfig as MemoryBankConfig,
)

memory_bank_config = MemoryBankConfig(
    customization_configs=[
        CustomizationConfig(
            memory_topics=[
                MemoryTopic(
                    managed_memory_topic=ManagedMemoryTopic(
                        managed_topic_enum=ManagedTopicEnum.KEY_CONVERSATION_DETAILS,
                    ),
                ),
                MemoryTopic(
                    managed_memory_topic=ManagedMemoryTopic(
                        managed_topic_enum=ManagedTopicEnum.EXPLICIT_INSTRUCTIONS,
                    ),
                ),
            ],
        ),
    ],
)
```

### Memory Topics

| Topic | What it captures | Example |
|-------|-----------------|---------|
| `KEY_CONVERSATION_DETAILS` | Issues discussed, resolutions, ticket outcomes, account interactions | "Customer had billing discrepancy on account cust_001. Ticket TKT-001 created with high priority." |
| `EXPLICIT_INSTRUCTIONS` | Direct instructions from the user about preferences or behavior | "Customer prefers Slack notifications. Always address as Alex." |

---

## Deployment Architecture

### Why deploy_agent.py (not agents-cli deploy)?

`agents-cli deploy` does not support `context_spec`, which is required to configure Memory Bank topics at deploy time. This is the documented agents-cli gap — the demo starts from an agents-cli scaffold but uses `deploy_agent.py` for the final deployment step.

```
agents-cli scaffold create     ← Used: project structure
agents-cli run                 ← Used: local testing (without Memory Bank)
agents-cli deploy              ← NOT used: cannot pass context_spec
deploy_agent.py                ← Used: deploys with Memory Bank config
```

### deploy.sh flow

```
deploy.sh
  │
  ├── Step 1: Create GCS staging bucket
  │   └── gcloud storage buckets create gs://<project>-sessions-demo-staging
  │       └── Idempotent: || echo "already exists"
  │
  ├── Step 2: Verify principal set IAM grants
  │   └── Informational — grants are in setup-project.sh
  │
  └── Step 3: Deploy via deploy_agent.py
      └── uv run python deploy_agent.py
          ├── Creates/updates agent via vertexai.Client
          ├── Passes context_spec with memory_bank_config
          ├── Sets telemetry env vars
          └── Writes deployment_metadata.json
```

### undeploy.sh flow

```
undeploy.sh
  │
  ├── Step 1: Delete agent from Agent Runtime
  │   ├── Read deployment_metadata.json
  │   ├── Extract reasoning engine ID
  │   ├── DELETE via REST API (force=true)
  │   └── Remove deployment_metadata.json
  │
  └── Step 2: Staging bucket cleanup
      └── Prints manual cleanup command
          (bucket may contain telemetry logs worth keeping)
```

### deploy_agent.py — key configuration

```python
config = AgentEngineConfig(
    displayName=display_name,
    stagingBucket=os.environ.get("STAGING_BUCKET", f"gs://{project_id}-sessions-demo-staging"),
    envVars=env_vars,                     # Telemetry + model config
    agentFramework="google-adk",
    identityType=IdentityType.AGENT_IDENTITY,
    contextSpec=context_spec,             # ← Memory Bank config
    source_packages=["./app"],            # Upload app/ directory
    entrypoint_module="app.agent_runtime_app",
    entrypoint_object="agent_runtime",
    requirements_file="app/app_utils/.requirements.txt",
    class_methods=class_methods,          # Generated from agent introspection
)
```

### Idempotency guarantees

| Operation | Idempotent? | Mechanism |
|-----------|-------------|-----------|
| Bucket creation | Yes | `\|\| echo "already exists"` |
| Agent deploy (first time) | Yes | `client.agent_engines.create()` |
| Agent deploy (update) | Yes | Detects existing by `display_name`, calls `update()` |
| Agent delete | Yes | `\|\| echo "Agent not found"` |
| Second deploy after undeploy | Yes | Creates fresh agent |

---

## Demo Scripts

### demo_stateless.py (Scenario A — no persistence)

Runs the agent locally with `InMemoryRunner`. No session service, no Memory Bank. Demonstrates what breaks:
- Session 1: Customer reports issue, agent saves preferences, creates ticket
- Session 2: Customer returns — agent has zero context, asks for name again

### demo_stateful.py (Scenario B — full persistence)

Calls the deployed agent via the Vertex AI API. Uses Agent Runtime's managed sessions and Memory Bank. Demonstrates what works:
- Session 1: Same interaction as Scenario A
- Session 2: Agent greets by name, recalls ticket, knows notification preference

### demo_full.py

Extended version with multiple sessions showing progressive memory accumulation.

---

## Design Decisions

### Why FunctionTools (not MCP)?

The demo focuses on sessions and memory, not tool governance. Python FunctionTools are simpler to understand and don't require deploying a separate MCP server. The tools are mock implementations returning deterministic data.

### Why after_agent_callback (not manual memory writes)?

`add_session_to_memory()` in the callback is the recommended pattern — it sends the entire session's events to Memory Bank for semantic extraction. This is simpler and more reliable than manually constructing memory entries.

### Why not use state interpolation in instructions?

ADK supports `{user:key}` interpolation in agent instructions, but it throws `KeyError` when the key doesn't exist (first-time users). Using `get_preferences` as a tool is more robust — it gracefully handles missing preferences.
