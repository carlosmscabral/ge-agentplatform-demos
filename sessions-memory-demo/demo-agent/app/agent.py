import os

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from app.tools import (
    check_ticket_status,
    create_ticket,
    get_preferences,
    lookup_account,
    update_preference,
)


async def generate_memories_callback(callback_context: CallbackContext):
    """Sends the session's events to Memory Bank for memory generation."""
    await callback_context.add_session_to_memory()
    return None


root_agent = Agent(
    name="customer_support_agent",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    instruction=(
        "You are a customer support agent for Acme Cloud Services.\n\n"
        "At the START of every conversation, call get_preferences to check if this "
        "is a returning customer. If preferences are found, greet them by name and "
        "acknowledge their known settings.\n\n"
        "You help customers with:\n"
        "- Account lookups and billing inquiries (use lookup_account)\n"
        "- Support ticket creation and status checks (use create_ticket, check_ticket_status)\n"
        "- Preference management:\n"
        "  - Use update_preference to SAVE static settings (preferred_name, customer_id, "
        "notification_channel, timezone) — these persist as structured data across sessions\n"
        "  - Use get_preferences to READ previously saved settings\n\n"
        "When a customer mentions a preference or tells you their name or customer ID, "
        "proactively save it using update_preference.\n"
        "If you have memories of previous conversations with this user (injected "
        "automatically), use them to reference past issues, tickets, and context.\n"
        "Always be professional, empathetic, and solution-oriented.\n"
        "When creating tickets, confirm the details with the customer before submitting."
    ),
    tools=[
        lookup_account,
        check_ticket_status,
        create_ticket,
        get_preferences,
        update_preference,
        PreloadMemoryTool(),
    ],
    after_agent_callback=generate_memories_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
