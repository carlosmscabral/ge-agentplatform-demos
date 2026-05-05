# demo-agent

ADK agent for the Sessions & Memory Bank demo. Customer support agent for "Acme Cloud Services" with persistent sessions and cross-session memory.

## Project Structure

```
demo-agent/
├── app/
│   ├── agent.py               # Agent definition + conditional Memory Bank
│   ├── agent_runtime_app.py   # Agent Runtime wrapper (AdkApp)
│   ├── tools.py               # 4 Python FunctionTools
│   ├── mock_data.py           # Mock customer/ticket data
│   └── app_utils/             # Telemetry and typing helpers
├── tests/                     # Integration tests and evals
├── scripts/                   # Demo scenario scripts (in parent dir)
└── pyproject.toml             # Project dependencies (ADK 1.32.0)
```

## Quick Start

```bash
agents-cli install && agents-cli dev
```

## Commands

| Command              | Description                                    |
| -------------------- | ---------------------------------------------- |
| `agents-cli install` | Install dependencies using uv                  |
| `agents-cli dev`     | Launch local development environment           |
| `agents-cli lint`    | Run code quality checks                        |
| `agents-cli test`    | Run unit and integration tests                 |
| `agents-cli deploy`  | Deploy agent to Agent Runtime                  |

## Deployment

See `../README.md` for full deployment instructions including Memory Bank setup.
