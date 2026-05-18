"""Mocked market data — no external API calls."""

QUOTES: dict[str, dict] = {
    "PETR4": {"ticker": "PETR4", "price": 38.42, "currency": "BRL", "change_pct": 1.23, "volume": 28_450_300},
    "VALE3": {"ticker": "VALE3", "price": 62.15, "currency": "BRL", "change_pct": -0.45, "volume": 15_200_800},
    "ITUB4": {"ticker": "ITUB4", "price": 33.78, "currency": "BRL", "change_pct": 0.62, "volume": 22_100_400},
    "AAPL": {"ticker": "AAPL", "price": 245.30, "currency": "USD", "change_pct": 2.18, "volume": 52_300_000},
    "GOOGL": {"ticker": "GOOGL", "price": 198.45, "currency": "USD", "change_pct": -0.32, "volume": 18_750_000},
    "MSFT": {"ticker": "MSFT", "price": 458.12, "currency": "USD", "change_pct": 0.85, "volume": 24_900_000},
}


def _make_hist(base: float, n: int = 60) -> list[dict]:
    out = []
    price = base
    for i in range(n):
        price = round(price * (1 + ((i % 7 - 3) * 0.004)), 2)
        out.append({"day": i, "close": price})
    return out


HIST: dict[str, list[dict]] = {t: _make_hist(q["price"]) for t, q in QUOTES.items()}

INDEXES: dict[str, dict] = {
    "IBOV": {"index": "IBOV", "value": 131_245.0, "change_pct": 0.34, "currency": "BRL"},
    "SP500": {"index": "SP500", "value": 6_142.50, "change_pct": 0.51, "currency": "USD"},
    "NASDAQ": {"index": "NASDAQ", "value": 22_810.20, "change_pct": 1.02, "currency": "USD"},
}
