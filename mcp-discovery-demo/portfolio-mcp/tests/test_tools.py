"""Unit tests for portfolio-mcp using FastMCP in-memory Client."""

import pytest
from fastmcp import Client

from app.main import mcp


@pytest.mark.asyncio
async def test_list_tools():
    async with Client(mcp) as c:
        names = {t.name for t in await c.list_tools()}
        assert names == {"get_portfolio_holdings", "get_position_pnl", "get_portfolio_allocation"}


@pytest.mark.asyncio
async def test_holdings_known_account():
    async with Client(mcp) as c:
        r = await c.call_tool("get_portfolio_holdings", {"account_id": "account-001"})
        assert r.data["account_id"] == "account-001"
        assert len(r.data["holdings"]) == 3


@pytest.mark.asyncio
async def test_holdings_unknown_account():
    async with Client(mcp) as c:
        r = await c.call_tool("get_portfolio_holdings", {"account_id": "nope"})
        assert "error" in r.data
        assert "available" in r.data


@pytest.mark.asyncio
async def test_pnl_positive():
    async with Client(mcp) as c:
        r = await c.call_tool("get_position_pnl", {"account_id": "account-001", "ticker": "PETR4"})
        assert r.data["pnl"] > 0  # mark > avg_cost


@pytest.mark.asyncio
async def test_pnl_position_not_held():
    async with Client(mcp) as c:
        r = await c.call_tool("get_position_pnl", {"account_id": "account-003", "ticker": "PETR4"})
        assert "error" in r.data


@pytest.mark.asyncio
async def test_allocation_sums_to_100():
    async with Client(mcp) as c:
        r = await c.call_tool("get_portfolio_allocation", {"account_id": "account-001"})
        total = sum(a["weight_pct"] for a in r.data["allocation"])
        assert abs(total - 100.0) < 0.05
