# Sessions & Memory Demo — Known Gaps

Last updated: 2026-05-05.

---

## Validated (Working)

- Agent with Python FunctionTools — no MCP server needed
- `InMemorySessionService` for local dev (Scenario A)
- `VertexAiSessionService` on Agent Runtime — automatic, no code config
- `user:` state scoping — preferences persist across sessions
- Memory Bank via `PreloadMemoryTool` + `after_agent_callback` — memories generated and recalled
- Memory Bank `context_spec` with topic configuration at deploy time
- `App()` wrapper required for Memory Bank integration

---

## Gap 1: `agents-cli deploy` does not support `context_spec`

Cannot use `agents-cli deploy` for Memory Bank deployments. Must use `deploy_agent.py` which calls `vertexai.Client` directly to pass `ReasoningEngineContextSpec(memory_bank_config=...)` in `AgentEngineConfig`.

---

## Resolved: State interpolation `{user:key?}` with optional `?` suffix

`{user:key}` (without `?`) throws `KeyError` when the key is absent. **Use `{user:key?}`** — the `?` suffix replaces missing keys with an empty string. This demo uses the `get_preferences` tool instead, but both approaches work.

---

## Gap 3: Memory generation has ~10-20s latency

`after_agent_callback` with `add_session_to_memory()` runs asynchronously. Memories may take 10-20 seconds to become searchable. Demo scripts account for this with a wait.
