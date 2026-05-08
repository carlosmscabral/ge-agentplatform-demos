import os

from google.adk.agents import Agent
from google.adk.apps import App


def lookup_order(order_id: str) -> dict:
    """Looks up the status and details of a customer order by its ID."""
    orders = {
        "ORD-123": {
            "order_id": "ORD-123",
            "status": "shipped",
            "items": ["Wireless Mouse", "USB-C Hub"],
            "estimated_delivery": "2026-05-12",
            "tracking_number": "1Z999AA10123456784",
        },
        "ORD-456": {
            "order_id": "ORD-456",
            "status": "delivered",
            "items": ["Mechanical Keyboard"],
            "estimated_delivery": "2026-05-06",
            "tracking_number": "1Z999AA10987654321",
        },
        "ORD-789": {
            "order_id": "ORD-789",
            "status": "processing",
            "items": ["Monitor Stand", "Webcam", "Desk Lamp"],
            "estimated_delivery": "2026-05-15",
            "tracking_number": None,
        },
    }
    if order_id in orders:
        return orders[order_id]
    return {"error": f"Order {order_id} not found."}


def search_faq(query: str) -> dict:
    """Searches the FAQ knowledge base for answers matching the query."""
    faqs = [
        {
            "question": "How do I reset my password?",
            "answer": "Go to Settings > Account > Reset Password. You will receive an email with a reset link.",
        },
        {
            "question": "What is the return policy?",
            "answer": "Items can be returned within 30 days of delivery for a full refund. Items must be in original packaging.",
        },
        {
            "question": "How do I track my order?",
            "answer": "Use the tracking number from your order confirmation email on the carrier's website.",
        },
        {
            "question": "What payment methods are accepted?",
            "answer": "We accept Visa, Mastercard, American Express, and PayPal.",
        },
    ]
    query_lower = query.lower()
    matches = [faq for faq in faqs if any(
        word in faq["question"].lower() or word in faq["answer"].lower()
        for word in query_lower.split()
    )]
    if matches:
        return {"results": matches[:2]}
    return {"results": [], "message": "No matching FAQ entries found."}


def create_ticket(subject: str, description: str) -> dict:
    """Creates a support ticket for issues that need human follow-up."""
    return {
        "ticket_id": "TKT-20260508-001",
        "status": "open",
        "subject": subject,
        "message": "Your support ticket has been created. A team member will respond within 24 hours.",
    }


root_agent = Agent(
    name="support_agent",
    model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
    instruction=(
        "You are a customer support assistant for an online electronics store. "
        "Use the available tools to help customers: "
        "- Use lookup_order to check order status when a customer asks about an order. "
        "- Use search_faq to find answers to common questions. "
        "- Use create_ticket when a customer has an issue that needs human follow-up "
        "(damaged items, billing disputes, complaints). "
        "Always be polite and concise."
    ),
    tools=[lookup_order, search_faq, create_ticket],
)

app = App(root_agent=root_agent, name="app")
