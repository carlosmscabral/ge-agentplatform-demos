"""FastMCP server: news + sentiment tools (mocked)."""

import os

from fastmcp import FastMCP

from app.fake_data import NEWS, SENTIMENT

mcp = FastMCP(name="fintoolkit-news-sentiment")


@mcp.tool()
def get_company_news(ticker: str, limit: int = 5) -> dict:
    """Recent news headlines for a ticker (most recent first)."""
    items = NEWS.get(ticker.upper(), [])
    if not items:
        return {"error": f"no news for '{ticker}'", "available": sorted(NEWS.keys())}
    limit = max(1, min(limit, 20))
    return {"ticker": ticker.upper(), "count": min(len(items), limit), "items": items[:limit]}


@mcp.tool()
def get_sentiment_score(ticker: str) -> dict:
    """Aggregate news sentiment for a ticker (score in [-1, 1], label, article count)."""
    s = SENTIMENT.get(ticker.upper())
    if not s:
        return {"error": f"no sentiment for '{ticker}'", "available": sorted(SENTIMENT.keys())}
    return {"ticker": ticker.upper(), **s}


@mcp.tool()
def search_news(query: str, limit: int = 5) -> dict:
    """Substring search across all headlines (case-insensitive). Returns matching items."""
    q = query.lower().strip()
    if not q:
        return {"error": "query is empty"}
    matches: list[dict] = []
    for tk, items in NEWS.items():
        for item in items:
            if q in item["headline"].lower():
                matches.append({"ticker": tk, **item})
    matches.sort(key=lambda m: m["date"], reverse=True)
    limit = max(1, min(limit, 20))
    return {"query": query, "count": min(len(matches), limit), "items": matches[:limit]}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    mcp.run(transport="http", host="0.0.0.0", port=port, path="/mcp")
