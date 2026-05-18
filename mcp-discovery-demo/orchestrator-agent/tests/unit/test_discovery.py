"""Unit tests for discovery.py — mocks AgentRegistry to verify filter logic."""

from unittest.mock import MagicMock, patch

import pytest

from app import discovery


_FAKE_REGISTRY_RESPONSE = {
    "mcpServers": [
        {
            "name": "projects/p/locations/us-central1/mcpServers/agentregistry-aaaa",
            "displayName": "market-data",
            "description": "[tag:market] [domain:finance] Market data MCP server: quotes, history, indexes.",
            "interfaces": [{"url": "https://market.run.app/mcp"}],
            "tools": [
                {"name": "get_stock_quote", "description": "Get latest quote for a ticker"},
                {"name": "get_market_index", "description": "Index snapshot"},
            ],
            "attributes": {},  # raw API doesn't return attributes; we parse from description
        },
        {
            "name": "projects/p/locations/us-central1/mcpServers/agentregistry-bbbb",
            "displayName": "portfolio",
            "description": "[tag:portfolio] [domain:finance] Portfolio MCP: holdings, PnL, allocation.",
            "interfaces": [{"url": "https://portfolio.run.app/mcp"}],
            "tools": [
                {"name": "get_portfolio_holdings", "description": "Account holdings"},
            ],
            "attributes": {},
        },
        {
            "name": "projects/p/locations/us-central1/mcpServers/agentregistry-cccc",
            "displayName": "news-sentiment",
            "description": "[tag:news] [domain:finance] News + sentiment MCP server.",
            "interfaces": [{"url": "https://news.run.app/mcp"}],
            "tools": [
                {"name": "get_sentiment_score", "description": "Sentiment for a ticker"},
            ],
            "attributes": {},
        },
    ]
}


def _patched_registry():
    """Return a mock _registry() that yields the fake response."""
    mock = MagicMock()
    mock.list_mcp_servers.return_value = _FAKE_REGISTRY_RESPONSE
    return mock


def setup_function(_func):
    discovery._registry.cache_clear()


def test_discover_by_intent_matches_display_name():
    with patch.object(discovery, "_registry", return_value=_patched_registry()):
        r = discovery.discover_tools_by_intent("portfolio")
    assert r["count"] == 1
    assert r["matches"][0]["display_name"] == "portfolio"
    assert "display_name" in r["matches"][0]["matched_in"]


def test_discover_by_intent_matches_description():
    with patch.object(discovery, "_registry", return_value=_patched_registry()):
        r = discovery.discover_tools_by_intent("quotes")  # only in market description
    assert r["count"] == 1
    assert r["matches"][0]["display_name"] == "market-data"
    assert "description" in r["matches"][0]["matched_in"]


def test_discover_by_intent_matches_tool_name():
    # "stock_quote" only appears in market-data's `get_stock_quote` tool name
    with patch.object(discovery, "_registry", return_value=_patched_registry()):
        r = discovery.discover_tools_by_intent("stock_quote")
    assert r["count"] == 1
    assert r["matches"][0]["display_name"] == "market-data"
    assert any(m.startswith("tool:get_stock_quote") for m in r["matches"][0]["matched_in"])


def test_discover_by_intent_matches_tool_description():
    # "Quote" appears in get_stock_quote's description, "Holdings" in get_portfolio_holdings
    with patch.object(discovery, "_registry", return_value=_patched_registry()):
        r = discovery.discover_tools_by_intent("holdings")
    assert r["count"] == 1
    assert r["matches"][0]["display_name"] == "portfolio"
    assert any("tool:get_portfolio_holdings" in m for m in r["matches"][0]["matched_in"])


def test_discover_by_intent_no_match_returns_empty():
    with patch.object(discovery, "_registry", return_value=_patched_registry()):
        r = discovery.discover_tools_by_intent("weather")
    assert r["count"] == 0
    assert r["matches"] == []


def test_discover_by_intent_empty_query():
    r = discovery.discover_tools_by_intent("")
    assert r.get("error") == "empty intent"


def test_discover_by_category_filters_by_tag():
    with patch.object(discovery, "_registry", return_value=_patched_registry()):
        r = discovery.discover_tools_by_category("news")
    assert r["count"] == 1
    assert r["matches"][0]["display_name"] == "news-sentiment"


def test_discover_by_category_empty_returns_error():
    r = discovery.discover_tools_by_category("")
    assert r.get("error") == "empty tag"


def test_discover_falls_back_to_empty_when_registry_unavailable():
    with patch.object(discovery, "_registry", return_value=None):
        r = discovery.discover_tools_by_intent("market")
    assert r["matches"] == []


# ─── invoke_mcp_tool ──────────────────────────────────────────────────────


def _make_async_tool(name, return_value):
    """Build a mock BaseTool-like object whose run_async returns the given value."""

    async def _run(args, tool_context):
        return return_value

    t = MagicMock()
    t.name = name
    t.run_async = _run
    return t


@pytest.mark.asyncio
async def test_invoke_mcp_tool_happy_path():
    import pytest_asyncio  # noqa: F401 — ensure plugin available

    # Mock the materialized toolset to return a fake tool list
    fake_tool = _make_async_tool("get_stock_quote", {"ticker": "AAPL", "price": 245.30})
    fake_toolset = MagicMock()

    async def _get_tools(*_args, **_kw):
        return [fake_tool]

    fake_toolset.get_tools = _get_tools

    # Patch the cache so _materialize_toolset short-circuits
    discovery._TOOLSET_CACHE.clear()
    discovery._TOOLSET_CACHE["projects/p/.../mcpServers/agentregistry-aaaa"] = fake_toolset

    result = await discovery.invoke_mcp_tool(
        mcp_server_name="projects/p/.../mcpServers/agentregistry-aaaa",
        tool_name="get_stock_quote",
        arguments={"ticker": "AAPL"},
    )
    assert result == {"result": {"ticker": "AAPL", "price": 245.30}}


@pytest.mark.asyncio
async def test_invoke_mcp_tool_unknown_tool_returns_error_with_available_list():
    fake_tool = _make_async_tool("get_stock_quote", {})
    fake_toolset = MagicMock()

    async def _get_tools(*_args, **_kw):
        return [fake_tool]

    fake_toolset.get_tools = _get_tools

    discovery._TOOLSET_CACHE.clear()
    discovery._TOOLSET_CACHE["mcpServers/x"] = fake_toolset

    result = await discovery.invoke_mcp_tool(
        mcp_server_name="mcpServers/x",
        tool_name="get_nonexistent",
        arguments={},
    )
    assert "error" in result
    assert result["available_tools"] == ["get_stock_quote"]


@pytest.mark.asyncio
async def test_invoke_mcp_tool_registry_unavailable():
    discovery._TOOLSET_CACHE.clear()
    with patch.object(discovery, "_registry", return_value=None):
        r = await discovery.invoke_mcp_tool(
            mcp_server_name="mcpServers/missing",
            tool_name="anything",
            arguments={},
        )
    assert "error" in r
    assert "failed to resolve MCP server" in r["error"]
