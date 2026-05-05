"""Scenario A: InMemorySessionService — no persistence, no memory.

Session 1: Customer reports billing issue. Agent discovers Enterprise plan,
           creates ticket, customer mentions Slack preference.
Session 2: Customer returns. Agent has NO context — asks to re-explain.

This demonstrates the "break" — why sessions and memory matter.

Usage:
    cd demo-agent
    uv run python ../scripts/demo_stateless.py
"""

import asyncio

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent

USER_ID = "demo_user"
APP_NAME = "sessions_memory_demo"

SESSION_1_MESSAGES = [
    "Hi, I'm having a billing issue. My customer ID is cust_001.",
    "Can you check the status of ticket T-1001?",
    "Please create a new high priority ticket about being overcharged $500 on my last invoice.",
    "By the way, I prefer Slack notifications. Can you save that preference?",
]

SESSION_2_MESSAGES = [
    "Hi, I'm following up on my billing issue from yesterday.",
    "Do you remember what ticket was created for me?",
    "What notification channel do I prefer?",
]


async def run_session(runner, session_service, session_label, messages):
    print(f"\n{'=' * 60}")
    print(f"  {session_label}")
    print(f"{'=' * 60}")

    session = await session_service.create_session(
        user_id=USER_ID, app_name=APP_NAME
    )
    print(f"  Session ID: {session.id}\n")

    for msg_text in messages:
        print(f"  Customer: {msg_text}")
        message = types.Content(
            role="user", parts=[types.Part.from_text(text=msg_text)]
        )
        response_text = ""
        async for event in runner.run_async(
            new_message=message,
            user_id=USER_ID,
            session_id=session.id,
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text
        print(f"  Agent:    {response_text[:500]}")
        print()


async def main():
    print()
    print("=" * 60)
    print("  SCENARIO A: InMemorySessionService — No Persistence")
    print("=" * 60)

    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        session_service=session_service,
        app_name=APP_NAME,
    )

    await run_session(
        runner, session_service,
        "SESSION 1: Initial Contact",
        SESSION_1_MESSAGES,
    )

    await run_session(
        runner, session_service,
        "SESSION 2: Follow-up (NEW session — context lost!)",
        SESSION_2_MESSAGES,
    )

    print()
    print("=" * 60)
    print("  RESULT: Agent had NO memory of Session 1 in Session 2.")
    print("  The customer had to re-explain everything.")
    print("=" * 60)
    print()


if __name__ == "__main__":
    asyncio.run(main())
