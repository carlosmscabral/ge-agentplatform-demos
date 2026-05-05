# Sessions & Memory on Agent Platform — A Tutorial

A hands-on guide to building agents that remember. Written from lessons learned building and deploying this demo to Google Agent Platform (May 2026).

---

## 1. The Problem: Amnesiac Agents

Out of the box, an ADK agent has no memory. Every conversation starts fresh — the agent doesn't know who the user is, what they asked before, or what they prefer. This is because the default `InMemorySessionService` creates isolated, ephemeral sessions.

```
SESSION 1                              SESSION 2

User: "I'm cust_001, call me Alex"     User: "Hi, following up on my issue"
Agent: "Hi Alex! I see you're on       Agent: "Hello! Could you provide
       the Enterprise plan..."                 your customer ID?"
User: "Create a ticket for $500                ^^^^^^^^^^^^^^^^^^^^
       overcharge"                             No idea who this is.
Agent: "Done! Ticket T-1003."                  Everything is gone.
```

There are actually **two distinct problems** here:

1. **Within a session**: The agent needs to track conversation context across turns (what the user just said, what tools were called, what state was accumulated). This is **session state**.

2. **Across sessions**: The agent needs to recall facts from *previous* conversations (user preferences, past issues, learned context). This is **memory**.

ADK solves these with two separate systems. Understanding the boundary between them is the key to building stateful agents.

---

## 2. Session State — The Four Scopes

Every ADK session carries a `state` dictionary. Keys in this dictionary are just strings, but ADK uses **prefix conventions** to control how long values survive:

```
                        Lifetime Diagram

    ┌─────────────────────────────────────────────────────────┐
    │                     app:                                │
    │  Shared across ALL users and ALL sessions. Global.      │
    │                                                         │
    │  ┌─────────────────────────────────────────────────┐    │
    │  │                  user:                           │    │
    │  │  Shared across all sessions for ONE user.       │    │
    │  │                                                  │    │
    │  │  ┌──────────────────────────────────────────┐   │    │
    │  │  │          (no prefix)                     │   │    │
    │  │  │  Lives for this session only.            │   │    │
    │  │  │                                          │   │    │
    │  │  │  ┌──────────────────────────────────┐   │   │    │
    │  │  │  │          temp:                   │   │   │    │
    │  │  │  │  Cleared after each turn.        │   │   │    │
    │  │  │  └──────────────────────────────────┘   │   │    │
    │  │  └──────────────────────────────────────────┘   │    │
    │  └─────────────────────────────────────────────────┘    │
    └─────────────────────────────────────────────────────────┘
```

| Prefix | Scope | Example | Survives Session End? |
|--------|-------|---------|-----------------------|
| *(none)* | This session | `state["current_ticket"] = "T-1003"` | No |
| `user:` | This user, all sessions | `state["user:preferred_name"] = "Alex"` | Yes |
| `app:` | All users, all sessions | `state["app:maintenance_mode"] = True` | Yes |
| `temp:` | This turn only | `state["temp:api_response"] = {...}` | No (cleared per turn) |

### Reading and writing state from tools

ADK auto-injects `tool_context` when a function's signature includes it. The `State` object on `tool_context.state` supports:

```python
# Writing — use [] or .get()
tool_context.state["user:preferred_name"] = "Alex"       # write
tool_context.state[f"user:{key}"] = value                 # write with dynamic key

# Reading — use .get() with a default
name = tool_context.state.get("user:preferred_name", "")  # read (returns "" if missing)
channel = tool_context.state["user:notification_channel"]  # read (KeyError if missing)
```

**`State` is not a dict.** It's a custom class that tracks deltas for persistence. It supports `[]`, `.get()`, `.__contains__()` (the `in` operator), `.setdefault()`, and `.update()` — but **not** `.items()`, `.keys()`, or `.values()`. To iterate over all keys, use `.to_dict()`:

```python
# Iterating — convert to dict first
for k, v in tool_context.state.to_dict().items():
    if k.startswith("user:"):
        print(f"{k} = {v}")
```

This demo uses both patterns:
- `update_preference` **writes** individual `user:` keys
- `get_preferences` **reads all** `user:` keys via `to_dict()` for returning customers

### How `user:` state is loaded in a new session

When `VertexAiSessionService` creates a new session for a `user_id`, it **automatically preloads** all `user:` keys from that user's previous sessions. You don't need to do anything — the values are already in `tool_context.state` from the first turn.

```
    Session 1 (user_id=alex)              Session 2 (user_id=alex)
    ────────────────────────              ────────────────────────
    update_preference writes:             State is pre-populated:
      user:preferred_name = "Alex"   ──►    user:preferred_name = "Alex"
      user:notification_channel = "slack"►  user:notification_channel = "slack"
      user:customer_id = "cust_001"  ──►    user:customer_id = "cust_001"

    Platform persists these              Your code reads them via:
    automatically at session end.        - tool_context.state.get("user:key")
                                         - get_preferences() tool
```

The values are *there* — but the LLM can't see them unless your code surfaces them. Three options:

1. **A tool** (used in this demo): `get_preferences` reads `user:` keys via `to_dict()` and returns them as a tool response. The LLM sees them as structured data. Best when you want the model to reason about all preferences at once.
2. **Instruction interpolation with `?`**: `{user:preferred_name?}` in the agent instruction. The `?` suffix makes the key optional — if it exists, its value is injected; if not, it's replaced with an empty string (no error). Without `?`, a missing key throws `KeyError` and crashes the agent.
   ```python
   instruction="Welcome back{user:preferred_name?}! How can I help?"
   # First-time user:     "Welcome back! How can I help?"
   # Returning user Alex: "Welcome backAlex! How can I help?"
   #                      (note: no space — design your template accordingly)
   ```
3. **A `before_agent_callback`**: Read state and inject it into context programmatically. Most control, but more code.

Same applies to `app:` keys — loaded for every session regardless of user.

### Important: state persistence depends on the SessionService

The `user:` prefix only *means* something if the `SessionService` backend actually persists it. With `InMemorySessionService`, everything — including `user:` keys — is lost when the process exits. With `VertexAiSessionService` (Agent Runtime), `user:` keys are stored in managed infrastructure and survive across sessions.

---

## 3. SessionService — Where State Lives

ADK provides pluggable session backends:

```
                      ┌──────────────────┐
                      │   Your Agent     │
                      │   (ADK Runner)   │
                      └────────┬─────────┘
                               │ reads/writes state
                               ▼
                    ┌─────────────────────┐
                    │   SessionService    │◄── plug in a backend
                    └─────────┬───────────┘
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
    ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐
    │  InMemory   │  │  Database    │  │  VertexAi        │
    │  (dev only) │  │  (Cloud SQL) │  │  (Agent Runtime) │
    └─────────────┘  └──────────────┘  └──────────────────┘
    Lost on restart   You manage DB    Fully managed by
                                       Agent Platform
```

| Service | When to use | State persistence |
|---------|-------------|-------------------|
| `InMemorySessionService` | Local dev, tests, Scenario A demo | None — process memory only |
| `DatabaseSessionService` | Cloud Run / GKE deployments | Cloud SQL or SQLite |
| `VertexAiSessionService` | Agent Runtime deployments | Managed by Agent Platform |

**On Agent Runtime, you don't configure the session service at all.** The platform wires `VertexAiSessionService` automatically when you deploy via `AdkApp`. This is the key simplification — no database to provision, no connection strings, no schema migrations.

---

## 4. Memory Bank — Learning Across Conversations

State tracks structured key-value pairs within sessions. **Memory Bank** does something fundamentally different: it extracts *semantic knowledge* from conversations and makes it available in future sessions.

```
                    State vs. Memory

    ┌────────────────────────────────────────────────┐
    │                  SESSION 1                     │
    │                                                │
    │  User: "Call me Alex, I prefer Slack"          │
    │                                                │
    │  State writes:                                 │
    │    user:preferred_name = "Alex"          ──────┼──► Structured KV
    │    user:notification_channel = "Slack"   ──────┼──► Available in
    │                                                │    next session
    │  Memory Bank extracts:                         │    via state
    │    "I prefer to be called Alex"          ──────┼──► Semantic text
    │    "I prefer Slack notifications"        ──────┼──► Injected into
    │    "I was overcharged $500, created       ─────┼──► system prompt
    │     ticket T-1003"                             │    via PreloadMemoryTool
    └────────────────────────────────────────────────┘

    ┌────────────────────────────────────────────────┐
    │                  SESSION 2                     │
    │                                                │
    │  PreloadMemoryTool fires automatically:        │
    │    System instruction gets appended with:      │
    │    <PAST_CONVERSATIONS>                        │
    │    I prefer to be called Alex                  │
    │    I prefer Slack notifications                │
    │    I was overcharged $500, created T-1003      │
    │    </PAST_CONVERSATIONS>                       │
    │                                                │
    │  User: "Do you remember me?"                   │
    │  Agent: "Welcome back, Alex! I recall you      │
    │          had ticket T-1003 about $500..."       │
    └────────────────────────────────────────────────┘
```

### What Memory Bank actually stores

In the console, Memory Bank looks deceptively simple: a flat list of free-text strings, each scoped to an `(app_name, user_id)` pair. That's because every memory is just a `fact` — a natural language sentence:

```
Scope: app=app, user_id=cli-user

Facts:
  "I prefer Slack notifications."
  "I was overcharged $500 on my last invoice and created a
   high-priority ticket (ID: T-1003) for it, with Customer ID: cust_001."
  "I prefer to be called Alex."
```

That's it. No structured fields, no typed columns. Just text, indexed for similarity search.

The **topics** (`USER_PREFERENCES`, `USER_PERSONAL_INFO`, etc.) are metadata tags, not rigid categories. They guide the extraction LLM on *what kinds of facts to look for* in the conversation, and a single memory can be tagged with multiple topics. For example, "I prefer to be called Alex" was tagged both `EXPLICIT_INSTRUCTIONS` and `USER_PERSONAL_INFO`. But the topics don't change how memories are stored or retrieved — `PreloadMemoryTool` searches all memories by similarity regardless of topic.

### Two tools for two jobs: state for structure, memory for semantics

| | State (`user:` prefix) | Memory Bank |
|---|---|---|
| **Data format** | Structured key-value pairs | Free-form text (natural language facts) |
| **What it stores** | Explicit values you set programmatically | Facts extracted automatically by an LLM |
| **How it's written** | `tool_context.state["user:key"] = value` | `after_agent_callback` → LLM extracts facts |
| **How it's read** | Direct key lookup (immediate, exact) | Similarity search (semantic, approximate) |
| **Best for** | Settings, IDs, toggles — things with known keys | Context, history, preferences expressed in conversation |
| **Latency** | Instant (available in next turn) | Async (10-20s for extraction) |
| **Example** | `user:notification_channel = "slack"` | `"I was overcharged $500 and created ticket T-1003"` |

Use **state** when you know the key ahead of time and want instant, exact access (e.g., `user:notification_channel`). Use **Memory Bank** when you want the platform to figure out what's worth remembering from a conversation (e.g., the agent automatically learning that the user had a billing dispute).

In this demo, we use **both**. Here's what session 2 looks like with both systems active:

```
SESSION 2 — Returning Customer

1. PreloadMemoryTool fires (automatic, before LLM sees the message):
   Injects into system instruction:
     "I was overcharged $500 on my last invoice and created
      ticket T-1003, with Customer ID cust_001."
     "I prefer to be called Alex."
     "I prefer Slack notifications."

2. Agent calls get_preferences (explicit tool call):
   Returns: {
     "preferred_name": "Alex",
     "customer_id": "cust_001",
     "notification_channel": "slack"
   }

3. Agent greets: "Welcome back, Alex! Let me check on ticket T-1003..."
```

Memory Bank gives the agent the *story* ("you had a $500 overcharge"). State gives the agent the *facts* (`customer_id = cust_001`). Together, the agent can both empathize and act.

---

## 5. The ADK Code Patterns

### 5.1 Agent wrapped in `App`

The agent must be wrapped in an `App` object — not exported as a raw `Agent`. `App` provides the runner-level wiring that connects session services, memory services, and tools.

```python
# app/agent.py
from google.adk.agents import Agent
from google.adk.apps import App

root_agent = Agent(
    name="customer_support_agent",
    model="gemini-3-flash-preview",
    instruction="...",
    tools=[...],
    after_agent_callback=generate_memories_callback,
)

# This wrapping is required. Just exporting root_agent won't work
# with Memory Bank on Agent Runtime.
app = App(root_agent=root_agent, name="app")
```

### 5.2 PreloadMemoryTool — automatic memory injection

`PreloadMemoryTool` is not a tool the model calls. It hooks into `process_llm_request` and runs *before every LLM call*. It takes the user's current message, searches Memory Bank for relevant memories, and appends them to the system instruction.

```python
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

root_agent = Agent(
    ...,
    tools=[
        lookup_account,        # Normal tool — model calls this
        create_ticket,         # Normal tool — model calls this
        PreloadMemoryTool(),   # NOT called by model — runs automatically
    ],
)
```

What happens under the hood:

```
User sends message
        │
        ▼
PreloadMemoryTool.process_llm_request()
        │
        ├── Extracts user query text
        ├── Calls tool_context.search_memory(query)
        ├── Gets matching memories from Memory Bank
        └── Appends to llm_request as system instruction:
            "The following content is from your previous
             conversations with the user..."

        │
        ▼
LLM sees: system instruction + past memories + user message
        │
        ▼
Model generates response (with full context)
```

The alternative is `LoadMemoryTool()` — this appears as a normal tool that the model can choose to call when it thinks memories might be relevant. `PreloadMemoryTool` is more reliable (always loads), `LoadMemoryTool` gives the model more control.

### 5.3 after_agent_callback — generating memories

After the agent finishes a turn, the `after_agent_callback` sends session events to Memory Bank for extraction:

```python
from google.adk.agents.callback_context import CallbackContext

async def generate_memories_callback(callback_context: CallbackContext):
    await callback_context.add_session_to_memory()
    return None

root_agent = Agent(
    ...,
    after_agent_callback=generate_memories_callback,
)
```

`add_session_to_memory()` sends all session events to the Memory Bank service. The platform then asynchronously:
1. Analyzes the conversation
2. Extracts facts matching configured topics (preferences, personal info, etc.)
3. Consolidates with existing memories (updates, not duplicates)
4. Stores them scoped to `(app_name, user_id)`

This is asynchronous — memories may take 10-20 seconds to become available.

### 5.4 update_preference — explicit state writes

For structured preferences you want available immediately (no 10-20s wait), write directly to `user:` state:

```python
def update_preference(key: str, value: str, tool_context: ToolContext) -> dict:
    tool_context.state[f"user:{key}"] = value  # Available instantly in next session
    return {"saved": key, "value": value}
```

---

## 6. Deploying to Agent Runtime

### What Agent Runtime gives you automatically

When you deploy an ADK agent to Agent Runtime (via `AdkApp`), these are **automatic**:

- `VertexAiSessionService` — managed session persistence, no config needed
- `user:` / `app:` state persistence — survives across sessions
- SPIFFE identity — per-agent IAM identity
- Cloud Trace — telemetry and tracing
- Health checks — readiness probes during deployment

### What you must configure explicitly

Memory Bank is **not automatic**. You must:

1. **Define a `MemoryBankConfig`** with the topics you want extracted
2. **Pass it as `context_spec`** in the `AgentEngineConfig` at deploy time
3. **Use `deploy_agent.py`** (not `agents-cli deploy`) because `agents-cli` doesn't support `context_spec`

### The deployment flow

```
                    Deploy Flow

    ┌────────────────────────────────────────────┐
    │            deploy_agent.py                  │
    │                                             │
    │  1. Build AgentEngineConfig:                │
    │     - source_packages: ["./app"]            │
    │     - entrypoint: app.agent_runtime_app     │
    │     - envVars, stagingBucket, etc.          │
    │     - identityType: AGENT_IDENTITY          │
    │     - contextSpec:                          │
    │         memory_bank_config:        ◄────────┼── This enables Memory Bank
    │           topics: [USER_PREFERENCES,        │
    │                    USER_PERSONAL_INFO, ...]  │
    │                                             │
    │  2. client.agent_engines.create(config=...) │
    └────────────────────┬────────────────────────┘
                         │
                         ▼
    ┌────────────────────────────────────────────┐
    │           Agent Runtime                     │
    │                                             │
    │  ┌─────────────────────────────────────┐   │
    │  │ VertexAiSessionService (automatic)  │   │
    │  │ - Creates/manages sessions          │   │
    │  │ - Persists user:/app: state         │   │
    │  └─────────────────────────────────────┘   │
    │                                             │
    │  ┌─────────────────────────────────────┐   │
    │  │ VertexAiMemoryBankService           │   │
    │  │ (enabled by context_spec)           │   │
    │  │ - Extracts memories from sessions   │   │
    │  │ - Serves similarity searches        │   │
    │  │ - Scoped by (app_name, user_id)     │   │
    │  └─────────────────────────────────────┘   │
    │                                             │
    │  ┌─────────────────────────────────────┐   │
    │  │ Your Agent Code                     │   │
    │  │ - app/agent.py (Agent + tools)      │   │
    │  │ - app/agent_runtime_app.py (AdkApp) │   │
    │  └─────────────────────────────────────┘   │
    └─────────────────────────────────────────────┘
```

---

## 7. Memory Bank Configuration

### Topics — extraction hints, not storage categories

Topics tell the extraction LLM *what kinds of facts to look for* in the conversation. They don't create separate buckets or tables — all memories are stored in the same flat index. A single memory can be tagged with multiple topics.

| Topic | Tells the LLM to look for | Example extracted fact |
|-------|---------------------------|----------------------|
| `USER_PERSONAL_INFO` | Names, relationships, dates | "I prefer to be called Alex" |
| `USER_PREFERENCES` | Likes, dislikes, preferred styles | "I prefer Slack notifications" |
| `KEY_CONVERSATION_DETAILS` | Milestones, outcomes, decisions | "Created ticket T-1003 for $500 overcharge" |
| `EXPLICIT_INSTRUCTIONS` | Things the user asks to remember/forget | "Always address me as Dr. Smith" |

If you omit a topic, the LLM won't look for that kind of fact. If you include all four, you get broader extraction. You can also define **custom topics** with your own labels and descriptions for domain-specific extraction.

### Defining the config

```python
# app/memory_config.py
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
                MemoryTopic(managed_memory_topic=ManagedMemoryTopic(
                    managed_topic_enum=ManagedTopicEnum.USER_PERSONAL_INFO)),
                MemoryTopic(managed_memory_topic=ManagedMemoryTopic(
                    managed_topic_enum=ManagedTopicEnum.USER_PREFERENCES)),
                MemoryTopic(managed_memory_topic=ManagedMemoryTopic(
                    managed_topic_enum=ManagedTopicEnum.KEY_CONVERSATION_DETAILS)),
                MemoryTopic(managed_memory_topic=ManagedMemoryTopic(
                    managed_topic_enum=ManagedTopicEnum.EXPLICIT_INSTRUCTIONS)),
            ],
        ),
    ],
)
```

### Passing it at deploy time

```python
# deploy_agent.py
from vertexai._genai.types import AgentEngineConfig, ReasoningEngineContextSpec
from app.memory_config import memory_bank_config

context_spec = ReasoningEngineContextSpec(
    memory_bank_config=memory_bank_config,
)

config = AgentEngineConfig(
    displayName="my-agent",
    contextSpec=context_spec,  # <-- This is the critical line
    source_packages=["./app"],
    entrypoint_module="app.agent_runtime_app",
    entrypoint_object="agent_runtime",
    ...
)

client.agent_engines.create(config=config)
```

Without `contextSpec`, the Agent Engine instance has no Memory Bank — `PreloadMemoryTool` will find no memories, and `add_session_to_memory()` will have nowhere to write.

---

## 8. The Deploy Machinery — What `deploy_agent.py` Actually Does

### Why not `agents-cli deploy`?

`agents-cli deploy` handles a lot automatically: it introspects your agent, generates metadata, uploads source code, and creates the Agent Engine. But it doesn't support `context_spec` — which is how you tell Agent Runtime to enable Memory Bank. So we need `deploy_agent.py`.

When you use `deploy_agent.py` with `source_packages` (uploading source code instead of pickling the agent object), the SDK requires you to provide three things that `agents-cli` normally generates for you:

1. **`source_packages`** — which directories to upload
2. **`entrypoint_module` / `entrypoint_object`** — where Agent Runtime finds your agent
3. **`class_methods`** — a manifest describing the agent's API surface

### What is `class_methods`?

Agent Runtime hosts your agent behind an HTTP API. When a client calls `stream_query`, `create_session`, or `register_feedback`, Agent Runtime needs to know:
- What methods exist
- What parameters each one takes (JSON schema)
- What API mode each uses (sync, async, streaming)

This manifest is `class_methods`. Here's a simplified view of what it contains:

```
class_methods = [
    {
        "name": "async_stream_query",     ◄── The main chat endpoint
        "api_mode": "async_stream",
        "parameters": {
            "properties": {
                "message": {"type": "string"},
                "user_id": {"type": "string"},
                "session_id": {"nullable": true, "type": "string"},
            },
            "required": ["message", "user_id"]
        }
    },
    {
        "name": "async_create_session",   ◄── Session management
        "api_mode": "async",
        "parameters": {
            "properties": {
                "user_id": {"type": "string"},
                "session_id": {"nullable": true, "type": "string"},
                "state": {"nullable": true, "type": "object"},
            },
            "required": ["user_id"]
        }
    },
    {
        "name": "register_feedback",      ◄── Custom operation (our code)
        "api_mode": "",
        "parameters": {
            "properties": {
                "feedback": {"type": "object"}
            },
            "required": ["feedback"]
        }
    },
    ... (14 methods total: sessions CRUD, query, memory, feedback)
]
```

### How it's generated

The `AdkApp` class defines its API surface via `register_operations()`. The SDK utility functions introspect this to build the manifest:

```python
from vertexai._genai import _agent_engines_utils

# Step 1: Ask the agent "what operations do you expose?"
registered_ops = _agent_engines_utils._get_registered_operations(agent=agent_runtime)
# Returns: {"": ["create_session", "register_feedback", ...],
#           "async": ["async_stream_query", ...], ...}

# Step 2: Generate JSON schemas for each method's parameters
class_methods_spec = _agent_engines_utils._generate_class_methods_spec_or_raise(
    agent=agent_runtime, operations=registered_ops,
)

# Step 3: Convert to plain dicts for the API
class_methods = [_agent_engines_utils._to_dict(m) for m in class_methods_spec]
```

This is the same code that `agents-cli deploy` runs internally. We're calling it directly because we need to combine it with `context_spec` in a single `AgentEngineConfig`.

### The full deployment picture

```
    deploy_agent.py
         │
         ├── 1. Introspect agent_runtime (AdkApp)
         │      └── Generate class_methods manifest (14 methods)
         │
         ├── 2. Build MemoryBankConfig
         │      └── Topics: USER_PREFERENCES, USER_PERSONAL_INFO, ...
         │
         ├── 3. Build AgentEngineConfig
         │      ├── source_packages: ["./app"]        ◄── Upload this code
         │      ├── entrypoint_module/object           ◄── Load this object
         │      ├── class_methods                      ◄── Expose these APIs
         │      ├── contextSpec.memory_bank_config     ◄── Enable Memory Bank
         │      ├── identityType: AGENT_IDENTITY       ◄── SPIFFE identity
         │      └── envVars, stagingBucket, ...         ◄── Runtime config
         │
         └── 4. client.agent_engines.create(config=...)
                └── Agent Runtime uploads code, starts container,
                    wires VertexAiSessionService + VertexAiMemoryBankService
```

### Could this be simpler?

Yes — if `agents-cli deploy` added `--context-spec` or `--memory-bank` flags, you wouldn't need `deploy_agent.py` at all. The `class_methods` generation, source packaging, and entrypoint configuration would all be handled automatically, just like they are for non-Memory-Bank deployments. This is tracked as [Gap 1](GAPS.md).

---

## 9. The Agent Runtime Wrapper (AdkApp)

`AdkApp` bridges your ADK agent code to the Agent Runtime hosting environment. It receives the `App` instance and handles lifecycle:

```python
# app/agent_runtime_app.py
from vertexai.agent_engines.templates.adk import AdkApp
from app.agent import app as adk_app  # This is the App() instance

class AgentEngineApp(AdkApp):
    def set_up(self) -> None:
        vertexai.init()
        setup_telemetry()
        super().set_up()  # Wires session service, memory service, etc.
        ...

agent_runtime = AgentEngineApp(
    app=adk_app,  # Pass the App instance, NOT the raw Agent
    artifact_service_builder=lambda: GcsArtifactService(...)
    # No memory_service_builder needed — handled by context_spec
)
```

Key points:
- Pass `app=adk_app` (the `App` wrapper), not `agent=root_agent`
- **Do not pass `memory_service_builder`** — Agent Runtime wires `VertexAiMemoryBankService` automatically when `context_spec.memory_bank_config` is set at deploy time
- `super().set_up()` initializes the session and memory services

---

## 10. The `__init__.py` Bootstrap

The `app/__init__.py` must set critical environment variables *before* importing the agent:

```python
# app/__init__.py
import os
import google.auth

_, project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")  # Required for preview models
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

from .agent import app  # Import AFTER env vars are set
```

Without these, the agent will try to use the Gemini API (not Vertex AI) and fail with an API key error.

---

## 11. Putting It All Together — The Complete Request Flow

```
                Complete Request Flow (Agent Runtime)

User sends message via agents-cli / API / Playground
                    │
                    ▼
    ┌───────────────────────────────────────────────┐
    │             Agent Runtime                      │
    │                                                │
    │  1. VertexAiSessionService                     │
    │     - Creates or resumes session               │
    │     - Loads user:/app: state from storage      │
    │                                                │
    │  2. Runner.run_async()                         │
    │     │                                          │
    │     ▼                                          │
    │  3. PreloadMemoryTool.process_llm_request()    │
    │     - Searches Memory Bank for relevant memories│
    │     - Appends to system instruction             │
    │     │                                          │
    │     ▼                                          │
    │  4. LLM generates response                     │
    │     - Sees: instruction + memories + user msg  │
    │     - May call tools (lookup_account, etc.)    │
    │     - May call update_preference → user: state │
    │     │                                          │
    │     ▼                                          │
    │  5. after_agent_callback fires                 │
    │     - add_session_to_memory()                  │
    │     - Memory Bank extracts facts asynchronously│
    │     │                                          │
    │     ▼                                          │
    │  6. VertexAiSessionService                     │
    │     - Persists session events                  │
    │     - Persists state changes (user: keys)      │
    │                                                │
    └───────────────────────────────────────────────┘
                    │
                    ▼
            Response to user
```

---

## 12. Inspecting State and Memories (Debugging)

### List sessions

```python
import vertexai
client = vertexai.Client(project="my-project", location="us-central1")
ae = "projects/.../reasoningEngines/123"

for s in client.agent_engines.sessions.list(name=ae):
    print(f"Session: {s.name}  user: {s.user_id}  state: {s.session_state}")
```

### List generated memories

```python
for m in client.agent_engines.memories.list(name=ae):
    print(f"Memory: {m.fact}")
```

### Search memories (similarity)

```python
results = client.agent_engines.memories.retrieve(
    name=ae,
    scope={"app_name": "app", "user_id": "cli-user"},
    similarity_search_params={"search_query": "notification preferences"},
)
for r in results:
    print(r)
```

### Manually trigger memory generation

Useful for testing or backfilling:

```python
client.agent_engines.memories.generate(
    name=ae,
    vertex_session_source={"session": f"{ae}/sessions/SESSION_ID"},
    scope={"app_name": "app", "user_id": "cli-user"},
)
```

---

## 13. Gotchas and Validated Findings

### State interpolation requires `?` for optional keys

ADK supports `{user:key}` in agent instructions to inject state values. Without `?`, a missing key throws `KeyError` and crashes the agent. **Always use `{user:key?}`** (with trailing `?`) for keys that may not exist — it replaces missing keys with an empty string instead of crashing. This is especially important for `user:` keys that won't exist on first contact with a new user.

Alternatively, use a tool like `get_preferences` to read state explicitly — this gives the model more structured data and avoids template formatting issues.

### `agents-cli deploy` does not support Memory Bank

`agents-cli deploy` cannot pass `context_spec` with `memory_bank_config`. You must use a `deploy_agent.py` script that calls `vertexai.Client.agent_engines.create()` directly with the full `AgentEngineConfig`.

### Memory generation is asynchronous (10-20s delay)

`after_agent_callback` triggers memory extraction, but the memories are not instantly available. In testing, 3 memories were generated within 20 seconds. Plan for this in demo scripts (add a brief wait between sessions).

### `App()` wrapper is required, not optional

Exporting a raw `Agent` as `app` (which the governance demo does) won't work with Memory Bank. The agent must be wrapped: `app = App(root_agent=root_agent, name="app")`. This provides the runner-level integration points for session and memory services.

### Agent Runtime manages everything — don't fight it

On Agent Runtime, you don't instantiate `VertexAiSessionService` or `VertexAiMemoryBankService` in your code. Don't pass `memory_service_builder` to `AdkApp`. The platform reads the `context_spec` from the Agent Engine resource and wires services automatically. Your code just needs `PreloadMemoryTool()` in the tools list and `after_agent_callback` on the agent.

### The `user_id` must be consistent across sessions

Memory Bank scopes memories by `(app_name, user_id)`. If different sessions use different `user_id` values, memories won't transfer. `agents-cli run` uses `cli-user` by default, which is consistent — but if you're building a frontend, ensure the same authenticated user gets the same `user_id`.

---

## 14. File Map — Where Everything Lives

```
sessions-memory-demo/
│
├── deploy.sh                    # Orchestration: bucket + IAM + deploy_agent.py
├── undeploy.sh                  # Teardown: delete agent + bucket instructions
├── .env.template                # Config template (PROJECT_ID, REGION, MODEL)
│
├── scripts/
│   ├── demo_stateless.py        # Scenario A: InMemory (shows the break)
│   └── demo_stateful.py         # Scenario B: Agent Runtime (shows the fix)
│
└── demo-agent/
    ├── deploy_agent.py          # Direct deploy with context_spec for Memory Bank
    ├── pyproject.toml            # ADK 1.32.0, aiplatform 1.149.0
    │
    └── app/
        ├── __init__.py           # Sets GOOGLE_CLOUD_* env vars before import
        ├── agent.py              # Agent + App wrapper + PreloadMemoryTool
        ├── agent_runtime_app.py  # AdkApp bridge to Agent Runtime
        ├── memory_config.py      # MemoryBankConfig with topic definitions
        ├── tools.py              # 4 FunctionTools (lookup, ticket, preference)
        ├── mock_data.py          # Mock customer/ticket data
        └── app_utils/
            ├── telemetry.py      # OpenTelemetry setup
            └── typing.py         # Feedback schema
```

---

## References

- [ADK Sessions Overview](https://adk.dev/sessions/)
- [ADK State Management](https://adk.dev/sessions/state/)
- [ADK Memory](https://adk.dev/sessions/memory/)
- [Agent Platform Sessions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/sessions)
- [Memory Bank Overview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank)
- [Memory Bank ADK Quickstart](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/adk-quickstart)
- [Memory Bank Setup](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank/setup)
- [ADK Samples: memory-bank](https://github.com/google/adk-samples/tree/main/python/agents/memory-bank)
