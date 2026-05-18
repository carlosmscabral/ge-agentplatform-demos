"""Unit tests for news-sentiment-mcp using FastMCP in-memory Client."""

import pytest
from fastmcp import Client

from app.main import mcp


@pytest.mark.asyncio
async def test_list_tools():
    async with Client(mcp) as c:
        names = {t.name for t in await c.list_tools()}
        assert names == {"get_company_news", "get_sentiment_score", "search_news"}


@pytest.mark.asyncio
async def test_get_company_news_known():
    async with Client(mcp) as c:
        r = await c.call_tool("get_company_news", {"ticker": "aapl"})
        assert r.data["ticker"] == "AAPL"
        assert r.data["count"] >= 1


@pytest.mark.asyncio
async def test_get_company_news_unknown():
    async with Client(mcp) as c:
        r = await c.call_tool("get_company_news", {"ticker": "ZZZZ"})
        assert "error" in r.data


@pytest.mark.asyncio
async def test_get_sentiment_score():
    async with Client(mcp) as c:
        r = await c.call_tool("get_sentiment_score", {"ticker": "AAPL"})
        assert r.data["ticker"] == "AAPL"
        assert -1.0 <= r.data["score"] <= 1.0
        assert r.data["label"] in {"very_positive", "positive", "neutral", "negative", "very_negative"}


@pytest.mark.asyncio
async def test_search_news_finds_match():
    async with Client(mcp) as c:
        r = await c.call_tool("search_news", {"query": "Copilot"})
        assert r.data["count"] >= 1
        assert any(m["ticker"] == "MSFT" for m in r.data["items"])


@pytest.mark.asyncio
async def test_search_news_empty_query():
    async with Client(mcp) as c:
        r = await c.call_tool("search_news", {"query": ""})
        assert "error" in r.data
