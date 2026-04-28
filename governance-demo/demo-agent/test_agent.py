import asyncio
import os
from app.agent import root_agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

async def main():
    session_service = InMemorySessionService()
    runner = Runner(app_name="test_app", agent=root_agent, session_service=session_service)
    
    # Run the agent
    # The signature indicated a regular Generator
    for event in runner.run(
        user_id="test_user",
        session_id="test_session",
        new_message="Check my account balance for user123"
    ):
        if hasattr(event, 'content') and event.content:
            print(event.content, end="", flush=True)
    print()

if __name__ == "__main__":
    asyncio.run(main())
