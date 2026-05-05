import os

import pytest
import pytest_asyncio
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def runner():
    session_service = InMemorySessionService()
    r = Runner(
        agent=root_agent,
        session_service=session_service,
        app_name="test",
    )
    return r, session_service


async def _run_and_collect(runner_obj, session_service, prompt):
    session = await session_service.create_session(
        user_id="test_user", app_name="test"
    )
    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=prompt)],
    )
    events = []
    async for event in runner_obj.run_async(
        new_message=message,
        user_id="test_user",
        session_id=session.id,
    ):
        events.append(event)
    return events


async def test_account_lookup(runner):
    runner_obj, session_service = runner
    events = await _run_and_collect(
        runner_obj, session_service, "Look up account cust_001"
    )
    assert len(events) > 0
    has_text = any(
        e.content and e.content.parts and any(p.text for p in e.content.parts)
        for e in events
    )
    assert has_text


async def test_ticket_status(runner):
    runner_obj, session_service = runner
    events = await _run_and_collect(
        runner_obj, session_service, "Check ticket T-1001"
    )
    assert len(events) > 0


async def test_create_ticket(runner):
    runner_obj, session_service = runner
    events = await _run_and_collect(
        runner_obj, session_service,
        "Create a high priority ticket for customer cust_001 about a billing overcharge of $500",
    )
    assert len(events) > 0
