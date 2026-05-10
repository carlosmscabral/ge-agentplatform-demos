"""Full demo: Session State vs Memory Bank — three acts.

Act 1 — First Visit:
    Customer introduces themselves, saves preferences, and creates a
    support ticket about a specific billing issue. This populates BOTH
    user: state (structured preferences) and Memory Bank (conversation
    details extracted by the LLM).

Act 2 — Session State Check:
    New session, same user. Agent should greet by name and recall
    preferences. This proves user: state persists across sessions.

Act 3 — Memory Bank Check:
    New session. Customer asks about past issues — something NOT stored
    in user: state. Only Memory Bank can answer "what was my last ticket
    about?" because it extracted that fact from the Act 1 conversation.

Usage:
    cd demo-agent
    uv run python ../scripts/cleanup_sessions_memories.py   # start clean
    uv run python ../scripts/demo_full.py
"""

import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEMO_AGENT_DIR = os.path.join(SCRIPT_DIR, "..", "demo-agent")
METADATA_FILE = os.path.join(DEMO_AGENT_DIR, "deployment_metadata.json")

# ── Act 1: First Visit ──────────────────────────────────────────────
ACT_1 = [
    "Hi! I'm Carlos, my customer ID is cust_001. I prefer Slack notifications.",
    "Can you look up my account details?",
    (
        "I just noticed I was charged twice on my last invoice — $500 extra. "
        "Please create a high priority ticket about this double billing."
    ),
    "Also, please remember: always contact me before making any billing changes to my account.",
]

# ── Act 2: Session State ────────────────────────────────────────────
ACT_2 = [
    "Hey, it's me again. Do you remember who I am?",
    "What notification channel do I prefer?",
]

# ── Act 3: Memory Bank ──────────────────────────────────────────────
ACT_3 = [
    "I had a support issue recently. Can you remind me what it was about?",
    "Did we create a ticket for it? What was the priority?",
]


def get_agent_url():
    if not os.path.exists(METADATA_FILE):
        print("ERROR: deployment_metadata.json not found.")
        print("Run ../deploy.sh first to deploy the agent.")
        sys.exit(1)
    with open(METADATA_FILE) as f:
        resource_name = json.load(f)["remote_agent_runtime_id"]
    # agents-cli run --url needs a full API URL, not just a resource name
    parts = resource_name.split("/")
    location = parts[3]
    return f"https://{location}-aiplatform.googleapis.com/v1beta1/{resource_name}"


def send(agent_url, message):
    result = subprocess.run(
        ["agents-cli", "run", "--url", agent_url, "--mode", "adk", message],
        capture_output=True,
        text=True,
        cwd=DEMO_AGENT_DIR,
    )
    if result.returncode != 0:
        return f"ERROR: {result.stderr.strip()}"
    # Strip agents-cli noise, keep only the final agent text
    lines = result.stdout.strip().split("\n")
    text_lines = [
        l for l in lines
        if not l.startswith(("Using project", "Querying remote", "[user]", "[tool_call", "[tool_response", "[customer_support_agent]: ", "Session: "))
    ]
    return "\n".join(text_lines).strip() or "(no text response)"


def run_act(agent_url, title, messages):
    print()
    print(f"{'─' * 62}")
    print(f"  {title}")
    print(f"{'─' * 62}")
    print()

    for msg in messages:
        print(f"  Customer: {msg}")
        response = send(agent_url, msg)
        # Trim long responses for readability
        lines = response.split("\n")
        if len(lines) > 8:
            response = "\n".join(lines[:8]) + "\n  ..."
        print(f"  Agent:    {response}")
        print()


def main():
    agent_url = get_agent_url()

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║   Session State vs Memory Bank — Full Demo                  ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"  Agent: {agent_url}")
    print()
    print("  This demo shows two persistence layers working together:")
    print("    • user: state  → structured preferences (name, channel)")
    print("    • Memory Bank  → conversation insights (past issues, tickets)")
    print()
    print("  Tip: run cleanup_sessions_memories.py first for a clean slate.")

    # ── Act 1 ────────────────────────────────────────────────────────
    run_act(agent_url, "ACT 1 — First Visit (save preferences + create ticket)", ACT_1)

    # ── Wait for Memory Bank ─────────────────────────────────────────
    wait_secs = 20
    print()
    print(f"  ⏳ Waiting {wait_secs}s for Memory Bank to extract facts...")
    print("     (after_agent_callback → add_session_to_memory runs async)")
    for i in range(wait_secs, 0, -5):
        print(f"     {i}s remaining...")
        time.sleep(5)
    print()

    # ── Act 2 ────────────────────────────────────────────────────────
    run_act(agent_url, "ACT 2 — Session State Check (user: preferences persist)", ACT_2)

    # ── Act 3 ────────────────────────────────────────────────────────
    run_act(agent_url, "ACT 3 — Memory Bank Check (conversation history recall)", ACT_3)

    # ── Summary ──────────────────────────────────────────────────────
    print()
    print("═" * 62)
    print("  WHAT JUST HAPPENED")
    print("═" * 62)
    print()
    print("  Act 1: Customer set preferences (user: state) and created a")
    print("         ticket about double billing (Memory Bank captures this).")
    print()
    print("  Act 2: New session. Agent recalled name and notification")
    print("         channel from user: state — structured key-value data")
    print("         that the agent explicitly saved via update_preference().")
    print()
    print("  Act 3: New session. Agent recalled the billing issue and")
    print("         ticket from Memory Bank — conversation details that")
    print("         were automatically extracted by the LLM, not stored")
    print("         as explicit preferences.")
    print()
    print("  Two layers, two purposes:")
    print("    user: state  = what the agent EXPLICITLY saves (preferences)")
    print("    Memory Bank  = what the LLM AUTOMATICALLY extracts (insights)")
    print()
    print("═" * 62)


if __name__ == "__main__":
    main()
