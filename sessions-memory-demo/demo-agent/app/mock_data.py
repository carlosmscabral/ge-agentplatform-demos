from datetime import datetime, timedelta

ACCOUNTS = {
    "cust_001": {
        "customer_id": "cust_001",
        "name": "Alex Thompson",
        "email": "alex.thompson@acmecorp.com",
        "plan_tier": "enterprise",
        "billing_status": "overdue",
        "monthly_charge": 2499.00,
        "feature_flags": ["advanced-analytics", "sso", "priority-support", "api-access"],
        "created_at": "2024-03-15",
    },
    "cust_002": {
        "customer_id": "cust_002",
        "name": "Jordan Rivera",
        "email": "jordan.r@startupinc.io",
        "plan_tier": "pro",
        "billing_status": "current",
        "monthly_charge": 149.00,
        "feature_flags": ["basic-analytics", "api-access"],
        "created_at": "2025-01-10",
    },
    "cust_003": {
        "customer_id": "cust_003",
        "name": "Sam Chen",
        "email": "sam.chen@freelance.dev",
        "plan_tier": "free",
        "billing_status": "current",
        "monthly_charge": 0.00,
        "feature_flags": [],
        "created_at": "2026-02-28",
    },
}

TICKETS = {
    "T-1001": {
        "ticket_id": "T-1001",
        "customer_id": "cust_001",
        "subject": "Billing discrepancy on March invoice",
        "description": "Charged $2,999 instead of $2,499. Requesting $500 credit.",
        "status": "open",
        "priority": "high",
        "created_at": (datetime.now() - timedelta(days=2)).isoformat(),
        "updates": [
            {
                "timestamp": (datetime.now() - timedelta(days=1)).isoformat(),
                "message": "Escalated to billing team for review.",
            }
        ],
    },
    "T-1002": {
        "ticket_id": "T-1002",
        "customer_id": "cust_002",
        "subject": "Feature request: custom dashboard widgets",
        "description": "Would like to create custom widgets on the analytics dashboard.",
        "status": "resolved",
        "priority": "low",
        "created_at": (datetime.now() - timedelta(days=14)).isoformat(),
        "updates": [
            {
                "timestamp": (datetime.now() - timedelta(days=7)).isoformat(),
                "message": "Added to Q3 roadmap. Custom widgets will be available in Pro tier.",
            }
        ],
    },
}

_next_ticket_id = 1003


def get_next_ticket_id() -> str:
    global _next_ticket_id
    tid = f"T-{_next_ticket_id}"
    _next_ticket_id += 1
    return tid
