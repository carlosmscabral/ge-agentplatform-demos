"""FastMCP server: portfolio tools (mocked)."""

import os

from fastmcp import FastMCP

from app.fake_data import HOLDINGS, MARKS

mcp = FastMCP(name="fintoolkit-portfolio")


@mcp.tool()
def get_portfolio_holdings(account_id: str) -> dict:
    """List all holdings (ticker, shares, avg_cost) for an account."""
    holdings = HOLDINGS.get(account_id)
    if holdings is None:
        return {"error": f"account '{account_id}' not found", "available": sorted(HOLDINGS.keys())}
    return {"account_id": account_id, "holdings": holdings}


@mcp.tool()
def get_position_pnl(account_id: str, ticker: str) -> dict:
    """Compute unrealized PnL for a single position (current mark vs avg cost)."""
    holdings = HOLDINGS.get(account_id)
    if holdings is None:
        return {"error": f"account '{account_id}' not found"}
    pos = next((h for h in holdings if h["ticker"] == ticker.upper()), None)
    if pos is None:
        return {"error": f"ticker '{ticker}' not held in {account_id}"}
    mark = MARKS.get(ticker.upper())
    if mark is None:
        return {"error": f"no mark price for '{ticker}'"}
    pnl = (mark - pos["avg_cost"]) * pos["shares"]
    pnl_pct = (mark / pos["avg_cost"] - 1) * 100
    return {
        "account_id": account_id,
        "ticker": ticker.upper(),
        "shares": pos["shares"],
        "avg_cost": pos["avg_cost"],
        "mark": mark,
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
    }


@mcp.tool()
def get_portfolio_allocation(account_id: str) -> dict:
    """Return % allocation per ticker based on current market value."""
    holdings = HOLDINGS.get(account_id)
    if holdings is None:
        return {"error": f"account '{account_id}' not found"}
    values = {h["ticker"]: h["shares"] * MARKS.get(h["ticker"], 0.0) for h in holdings}
    total = sum(values.values()) or 1.0
    allocation = [{"ticker": t, "market_value": round(v, 2), "weight_pct": round(v / total * 100, 2)} for t, v in values.items()]
    return {"account_id": account_id, "total_market_value": round(total, 2), "allocation": allocation}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
