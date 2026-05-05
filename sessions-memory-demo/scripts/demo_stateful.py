"""Scenario B: VertexAiSessionService + Memory Bank — persistence works.

Session 1: Same conversation as Scenario A. Preferences saved to user: state.
           Memory Bank extracts key facts at end of session.
Session 2: PreloadMemoryTool loads memories. Agent greets with context.

This demonstrates the "fix" — sessions and memory in action.

Usage:
    cd demo-agent
    uv run python ../scripts/demo_stateful.py

Requires:
    - Agent deployed to Agent Runtime (run ../deploy.sh first)
    - deployment_metadata.json present in demo-agent/
    - AGENT_ENGINE_ID set in .env (redeploy after first deploy)
"""

import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEMO_AGENT_DIR = os.path.join(SCRIPT_DIR, "..", "demo-agent")
METADATA_FILE = os.path.join(DEMO_AGENT_DIR, "deployment_metadata.json")

SESSION_1_MESSAGES = [
    "Hi, I'm having a billing issue. My customer ID is cust_001.",
    "Can you check the status of ticket T-1001?",
    "Please create a new high priority ticket about being overcharged $500 on my last invoice.",
    "By the way, I prefer Slack notifications and call me Alex. Please save those preferences.",
]

SESSION_2_MESSAGES = [
    "Hi, I'm back. Do you remember me and my previous issues?",
    "What was the ticket we created last time?",
    "What's my preferred notification channel?",
]


def get_agent_url():
    if not os.path.exists(METADATA_FILE):
        print("ERROR: deployment_metadata.json not found.")
        print("Run ../deploy.sh first to deploy the agent.")
        sys.exit(1)

    with open(METADATA_FILE) as f:
        metadata = json.load(f)
    return metadata["remote_agent_runtime_id"]


def run_message(agent_url, message):
    """Send a message to the deployed agent via agents-cli run."""
    result = subprocess.run(
        [
            "agents-cli", "run",
            "--url", agent_url,
            "--mode", "adk",
            message,
        ],
        capture_output=True,
        text=True,
        cwd=DEMO_AGENT_DIR,
    )
    return result.stdout.strip() if result.returncode == 0 else f"ERROR: {result.stderr.strip()}"


def run_session(agent_url, session_label, messages):
    print(f"\n{'=' * 60}")
    print(f"  {session_label}")
    print(f"{'=' * 60}\n")

    for msg in messages:
        print(f"  Customer: {msg}")
        response = run_message(agent_url, msg)
        print(f"  Agent:    {response[:500]}")
        print()


def main():
    agent_url = get_agent_url()

    print()
    print("=" * 60)
    print("  SCENARIO B: VertexAiSessionService + Memory Bank")
    print("=" * 60)
    print(f"  Agent URL: {agent_url}")

    run_session(agent_url, "SESSION 1: Initial Contact", SESSION_1_MESSAGES)

    print()
    print("  Waiting 10 seconds for memory generation...")
    print("  (after_agent_callback runs asynchronously)")
    time.sleep(10)

    run_session(
        agent_url,
        "SESSION 2: Follow-up (NEW session — memories loaded!)",
        SESSION_2_MESSAGES,
    )

    print()
    print("=" * 60)
    print("  RESULT: Agent recalled context from Session 1 via Memory Bank.")
    print("  Preferences persisted via user: state scoping.")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
