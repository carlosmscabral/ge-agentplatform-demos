"""Unit tests for market-data-mcp using FastMCP in-memory Client."""

import pytest
from fastmcp import Client

from app.main import mcp


@pytest.mark.asyncio
async def test_list_tools_exposes_three():
    async with Client(mcp) as c:
        tools = await c.list_tools()
        names = {t.name for t in tools}
        assert names == {"get_stock_quote", "get_historical_prices", "get_market_index"}


@pytest.mark.asyncio
async def test_get_stock_quote_known_ticker():
    async with Client(mcp) as c:
        r = await c.call_tool("get_stock_quote", {"ticker": "petr4"})
        assert r.data["ticker"] == "PETR4"
        assert "price" in r.data


@pytest.mark.asyncio
async def test_get_stock_quote_unknown_returns_error():
    async with Client(mcp) as c:
        r = await c.call_tool("get_stock_quote", {"ticker": "XXXX"})
        assert "error" in r.data
        assert "available" in r.data


@pytest.mark.asyncio
async def test_get_historical_prices_default_30():
    async with Client(mcp) as c:
        r = await c.call_tool("get_historical_prices", {"ticker": "AAPL"})
        assert r.data["days"] == 30
        assert len(r.data["series"]) == 30


@pytest.mark.asyncio
async def test_get_historical_prices_clamps_max_60():
    async with Client(mcp) as c:
        r = await c.call_tool("get_historical_prices", {"ticker": "AAPL", "days": 500})
        assert r.data["days"] == 60


@pytest.mark.asyncio
async def test_get_market_index():
    async with Client(mcp) as c:
        r = await c.call_tool("get_market_index", {"index": "ibov"})
        assert r.data["index"] == "IBOV"
        assert "value" in r.data
