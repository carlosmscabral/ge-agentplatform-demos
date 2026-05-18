"""Mocked portfolio data keyed by account_id."""

HOLDINGS: dict[str, list[dict]] = {
    "account-001": [
        {"ticker": "PETR4", "shares": 500, "avg_cost": 32.10},
        {"ticker": "VALE3", "shares": 200, "avg_cost": 58.40},
        {"ticker": "AAPL", "shares": 50, "avg_cost": 210.00},
    ],
    "account-002": [
        {"ticker": "ITUB4", "shares": 1000, "avg_cost": 30.20},
        {"ticker": "MSFT", "shares": 80, "avg_cost": 420.00},
    ],
    "account-003": [
        {"ticker": "GOOGL", "shares": 100, "avg_cost": 185.00},
    ],
}

# Latest mark prices used to compute PnL (mirrors market-data quotes — kept local for isolation).
MARKS: dict[str, float] = {
    "PETR4": 38.42,
    "VALE3": 62.15,
    "ITUB4": 33.78,
    "AAPL": 245.30,
    "GOOGL": 198.45,
    "MSFT": 458.12,
}
