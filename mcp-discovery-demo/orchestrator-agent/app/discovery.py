"""Agent Registry discovery helpers.

Two discovery patterns are exposed as plain functions (later wrapped as FunctionTools
in `agent.py`):

  * `discover_tools_by_intent(intent)` — substring match on display name + description.
    AgentRegistry does NOT have semantic/embedding search; we filter by attribute.
  * `discover_tools_by_category(tag)` — filter by the `tag=` attribute that
    deploy.sh sets when registering each MCP server.

Each function returns a JSON-serializable list of {name, display_name, description,
tools, url, attributes} dicts. If the registry is unreachable (local dev), an empty
list is returned and the caller falls back to env-var-defined URLs.
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"\[(\w+):([^\]]+)\]")


def _parse_attributes(description: str) -> dict[str, str]:
    """Extract `[key:value]` markers from the description.

    gcloud alpha agent-registry services create does NOT expose --attributes
    or --labels, so deploy.sh encodes the category tag in the description
    (e.g. `[tag:market] [domain:finance] ...`). We parse them here.
    """
    return {m.group(1): m.group(2).strip() for m in _TAG_RE.finditer(description or "")}


@lru_cache(maxsize=1)
def _registry():
    """Lazy-init AgentRegistry; cached for process lifetime."""
    try:
        from google.adk.integrations.agent_registry import AgentRegistry
        from google.auth import default
    except ImportError as e:
        logger.warning("AgentRegistry import failed: %s", e)
        return None

    _, project_id = default()
    if not project_id:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = os.environ.get("REGISTRY_LOCATION", "us-central1")
    if not project_id:
        logger.warning("No project_id available — AgentRegistry will not be created")
        return None
    return AgentRegistry(project_id=project_id, location=location)


def _normalize(server: dict[str, Any]) -> dict[str, Any]:
    """Flatten the registry MCPServer schema into something the LLM can read.

    The Registry API returns tools at the TOP level of the MCPServer resource
    (read-only, populated from the toolspec content provided at create time).
    Don't look inside `mcpServerSpec.toolSpec.tools` — that field is the create
    envelope and isn't echoed back.
    """
    interfaces = server.get("interfaces", []) or []
    url = interfaces[0].get("url", "") if interfaces else ""
    raw_tools = server.get("tools", []) or []
    tools = [{"name": t.get("name"), "description": t.get("description", "")} for t in raw_tools]
    description = server.get("description", "")
    # Attributes: parse `[key:value]` markers from description (Registry's own
    # `attributes` field is system-reserved — see _parse_attributes docstring).
    attrs = dict(server.get("attributes", {}) or {})
    attrs.update(_parse_attributes(description))
    return {
        "name": server.get("name", ""),
        "display_name": server.get("displayName", ""),
        "description": description,
        "url": url,
        "tools": tools,
        "attributes": attrs,
    }


def _list_all() -> list[dict[str, Any]]:
    reg = _registry()
    if reg is None:
        return []
    try:
        resp = reg.list_mcp_servers()
    except Exception:
        logger.exception("list_mcp_servers failed")
        return []
    return [_normalize(s) for s in resp.get("mcpServers", [])]


def build_toolset_from_registry(mcp_server_name: str):
    """Materialize an McpToolset by resolving its URL from Agent Registry.

    This is the load-bearing call that makes Registry the source of truth for
    runtime tool resolution — instead of baking URLs into env vars at deploy
    time. `registry.get_mcp_toolset` GETs the MCPServer resource, reads the
    `interfaces[].url` field, and returns a configured `McpToolset`.

    The tool name prefix is derived from the MCPServer's `displayName` by the
    registry (e.g. `market-data` → tools like `market_data_get_stock_quote`).
    We don't override it — the prefix should match the discovery output so the
    LLM can correlate "this is from MCP X" with the tool names it sees.

    For non-Google-API URLs (e.g. Cloud Run `*.run.app`), the toolset's auto
    header provider does NOT inject Google auth — so this is safe with public
    Cloud Run MCPs (--allow-unauthenticated). For private endpoints behind
    Agent Gateway, the registry's `bindings` field auto-resolves the auth
    scheme to `GcpAuthProviderScheme`.
    """
    reg = _registry()
    if reg is None:
        raise RuntimeError("AgentRegistry unavailable — cannot resolve MCP server")
    return reg.get_mcp_toolset(mcp_server_name)


def discover_tools_by_intent(intent: str) -> dict[str, Any]:
    """Return MCP servers whose displayName, description, or any tool's name /
    description contains the intent keyword (case-insensitive substring).

    Unlike the Registry's native `searchMcpServers` (which only knows about
    `mcpServerId | name | displayName` — verified against the v1alpha discovery
    doc), this function ALSO searches the tools inside each MCP, so an intent
    like "quote" finds `market-data` (whose tool `get_stock_quote` matches),
    and "sentiment" finds `news-sentiment` (its `get_sentiment_score` tool).

    Each match includes a `matched_in` field listing where the keyword hit
    (`display_name`, `description`, `tool:<name>:name`, `tool:<name>:description`),
    so the LLM can explain its choice.

    Args:
        intent: Free-text keyword extracted from the user's question
                (e.g. "sentiment", "quote", "allocation", "news").
    Returns:
        Dict with `criterion`, `query`, `matches` (server dicts with extra
        `matched_in` field), `count`.
    """
    q = (intent or "").strip().lower()
    if not q:
        return {"criterion": "intent", "query": intent, "matches": [], "error": "empty intent"}

    matches: list[dict[str, Any]] = []
    for server in _list_all():
        hits: list[str] = []
        if q in (server.get("display_name") or "").lower():
            hits.append("display_name")
        if q in (server.get("description") or "").lower():
            hits.append("description")
        for tool in server.get("tools", []) or []:
            tname = (tool.get("name") or "").lower()
            tdesc = (tool.get("description") or "").lower()
            if q in tname:
                hits.append(f"tool:{tool.get('name')}:name")
            if q in tdesc:
                hits.append(f"tool:{tool.get('name')}:description")
        if hits:
            enriched = dict(server)
            enriched["matched_in"] = hits
            matches.append(enriched)

    return {"criterion": "intent", "query": intent, "matches": matches, "count": len(matches)}


def discover_tools_by_category(tag: str) -> dict[str, Any]:
    """Return MCP servers tagged with the given category attribute.

    Args:
        tag: A category like "market", "portfolio", or "news"
             (matches the `tag` attribute set by deploy.sh).
    Returns:
        Dict with 'criterion', 'tag', 'matches' (list of server dicts).
    """
    t = (tag or "").strip().lower()
    if not t:
        return {"criterion": "category", "tag": tag, "matches": [], "error": "empty tag"}
    matches = [s for s in _list_all() if str(s["attributes"].get("tag", "")).lower() == t]
    return {"criterion": "category", "tag": tag, "matches": matches, "count": len(matches)}
