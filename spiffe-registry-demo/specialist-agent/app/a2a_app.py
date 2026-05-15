"""Local A2A server for testing the specialist agent.

Usage:
    cd specialist-agent
    uv run uvicorn app.a2a_app:a2a_app --host 0.0.0.0 --port 8001

Verify agent card:
    curl http://localhost:8001/.well-known/agent.json
"""

import os

from google.adk.a2a.utils.agent_to_a2a import to_a2a

from app.agent import root_agent

a2a_app = to_a2a(
    root_agent,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8001")),
)
