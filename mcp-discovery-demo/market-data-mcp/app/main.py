"""FastMCP server: market data tools (mocked)."""

import os

from fastmcp import FastMCP

from app.fake_data import HIST, INDEXES, QUOTES

mcp = FastMCP(name="fintoolkit-market-data")


@mcp.tool()
def get_stock_quote(ticker: str) -> dict:
    """Get latest quote (price, change, volume) for a stock ticker (e.g. PETR4, AAPL)."""
    q = QUOTES.get(ticker.upper())
    if not q:
        return {"error": f"ticker '{ticker}' not found", "available": sorted(QUOTES.keys())}
    return q


@mcp.tool()
def get_historical_prices(ticker: str, days: int = 30) -> dict:
    """Get last N days of closing prices for a ticker. Default 30, max 60."""
    series = HIST.get(ticker.upper())
    if not series:
        return {"error": f"ticker '{ticker}' not found"}
    days = max(1, min(days, 60))
    return {"ticker": ticker.upper(), "days": days, "series": series[-days:]}


@mcp.tool()
def get_market_index(index: str) -> dict:
    """Snapshot of a market index (IBOV, SP500, NASDAQ)."""
    idx = INDEXES.get(index.upper())
    if not idx:
        return {"error": f"index '{index}' not found", "available": sorted(INDEXES.keys())}
    return idx


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
