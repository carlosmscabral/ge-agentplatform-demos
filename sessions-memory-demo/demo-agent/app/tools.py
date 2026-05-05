from datetime import datetime

from google.adk.tools import ToolContext

from app.mock_data import ACCOUNTS, TICKETS, get_next_ticket_id


def lookup_account(customer_id: str) -> dict:
    """Look up a customer account by ID. Returns plan tier, billing status, and features."""
    account = ACCOUNTS.get(customer_id)
    if not account:
        return {"error": "Account not found", "customer_id": customer_id}
    return account


def check_ticket_status(ticket_id: str) -> dict:
    """Check the status of a support ticket by its ID."""
    ticket = TICKETS.get(ticket_id)
    if not ticket:
        return {"error": "Ticket not found", "ticket_id": ticket_id}
    return ticket


def create_ticket(
    customer_id: str,
    subject: str,
    description: str,
    priority: str,
) -> dict:
    """Create a new support ticket for a customer.

    Args:
        customer_id: The customer's account ID (e.g. cust_001).
        subject: Brief summary of the issue.
        description: Detailed description of the problem.
        priority: One of: low, medium, high, critical.
    """
    if customer_id not in ACCOUNTS:
        return {"error": "Account not found", "customer_id": customer_id}

    valid_priorities = ("low", "medium", "high", "critical")
    if priority not in valid_priorities:
        return {"error": f"Invalid priority. Must be one of: {', '.join(valid_priorities)}"}

    ticket_id = get_next_ticket_id()
    TICKETS[ticket_id] = {
        "ticket_id": ticket_id,
        "customer_id": customer_id,
        "subject": subject,
        "description": description,
        "status": "open",
        "priority": priority,
        "created_at": datetime.now().isoformat(),
        "updates": [],
    }
    return {
        "ticket_id": ticket_id,
        "status": "created",
        "message": f"Ticket {ticket_id} created successfully",
    }


def get_preferences(tool_context: ToolContext) -> dict:
    """Retrieve all saved customer preferences from previous sessions.

    Call this at the start of a conversation to check if the customer
    is a returning user with known preferences (name, notification channel, etc.).
    """
    prefs = {
        k.removeprefix("user:"): v
        for k, v in tool_context.state.to_dict().items()
        if k.startswith("user:")
    }
    return prefs if prefs else {"message": "No saved preferences found for this user."}


def update_preference(
    key: str,
    value: str,
    tool_context: ToolContext,
) -> dict:
    """Save a customer preference that persists across sessions.

    Args:
        key: Preference name (e.g. notification_channel, preferred_name, timezone).
        value: Preference value (e.g. slack, Alex, America/Los_Angeles).
    """
    tool_context.state[f"user:{key}"] = value
    return {"saved": key, "value": value, "scope": "user (cross-session)"}
