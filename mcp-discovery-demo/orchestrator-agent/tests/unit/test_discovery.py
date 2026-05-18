"""Unit tests for discovery.py — mocks AgentRegistry to verify filter logic."""

from unittest.mock import MagicMock, patch

from app import discovery


_FAKE_REGISTRY_RESPONSE = {
    "mcpServers": [
        {
            "name": "projects/p/locations/us-central1/mcpServers/agentregistry-aaaa",
            "displayName": "market-data",
            "description": "[tag:market] [domain:finance] Market data MCP server: quotes, history, indexes.",
            "interfaces": [{"url": "https://market.run.app/mcp"}],
            "mcpServerSpec": {"toolSpec": {"tools": [
                {"name": "get_stock_quote", "description": "Quote"},
                {"name": "get_market_index", "description": "Index"},
            ]}},
            "attributes": {},  # raw API doesn't return attributes; we parse from description
        },
        {
            "name": "projects/p/locations/us-central1/mcpServers/agentregistry-bbbb",
            "displayName": "portfolio",
            "description": "[tag:portfolio] [domain:finance] Portfolio MCP: holdings, PnL, allocation.",
            "interfaces": [{"url": "https://portfolio.run.app/mcp"}],
            "mcpServerSpec": {"toolSpec": {"tools": [
                {"name": "get_portfolio_holdings", "description": "Holdings"},
            ]}},
            "attributes": {},
        },
        {
            "name": "projects/p/locations/us-central1/mcpServers/agentregistry-cccc",
            "displayName": "news-sentiment",
            "description": "[tag:news] [domain:finance] News + sentiment MCP server.",
            "interfaces": [{"url": "https://news.run.app/mcp"}],
            "mcpServerSpec": {"toolSpec": {"tools": [
                {"name": "get_sentiment_score", "description": "Sentiment"},
            ]}},
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


def test_discover_by_intent_matches_substring():
    with patch.object(discovery, "_registry", return_value=_patched_registry()):
        r = discovery.discover_tools_by_intent("portfolio")
    assert r["count"] == 1
    assert r["matches"][0]["display_name"] == "portfolio"


def test_discover_by_intent_matches_description_too():
    with patch.object(discovery, "_registry", return_value=_patched_registry()):
        r = discovery.discover_tools_by_intent("quotes")  # only in market description
    assert r["count"] == 1
    assert r["matches"][0]["display_name"] == "market-data"


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
