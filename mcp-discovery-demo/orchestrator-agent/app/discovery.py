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


_TOOLSET_CACHE: dict[str, Any] = {}


def _materialize_toolset(mcp_server_name: str):
    """Materialize a *no-prefix* McpToolset for a Registry MCPServer (cached).

    We deliberately bypass `registry.get_mcp_toolset()` because that helper
    auto-applies a tool-name prefix derived from `displayName` (e.g.,
    `market-data` → tools become `market_data_get_stock_quote`). For the
    dynamic-invoker pattern we want tool names to match exactly what discovery
    returned (bare `get_stock_quote`), so the LLM can pass them through
    unchanged. We resolve the URL ourselves and build the toolset directly.
    """
    if mcp_server_name in _TOOLSET_CACHE:
        return _TOOLSET_CACHE[mcp_server_name]

    from google.adk.tools.mcp_tool import McpToolset
    from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

    reg = _registry()
    if reg is None:
        raise RuntimeError("AgentRegistry unavailable — cannot resolve MCP server")
    server_details = reg.get_mcp_server(mcp_server_name)
    interfaces = server_details.get("interfaces", []) or []
    url = interfaces[0].get("url", "") if interfaces else ""
    if not url:
        raise RuntimeError(f"MCPServer {mcp_server_name} has no usable interfaces[].url")

    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=url),
        tool_name_prefix=None,  # keep tool names verbatim
    )
    _TOOLSET_CACHE[mcp_server_name] = toolset
    logger.info("Materialized + cached toolset for %s → %s", mcp_server_name, url)
    return toolset


async def invoke_mcp_tool(
    mcp_server_name: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    tool_context: Any = None,
) -> dict[str, Any]:
    """Invoke a tool on an MCP server resolved via Agent Registry.

    Use this AFTER `discover_tools_by_intent` or `discover_tools_by_category`
    has told you which MCP server (by its `name` field) and which `tool`
    (by name) to invoke.

    Args:
        mcp_server_name: The full Registry resource name returned by discovery,
                         e.g. `projects/{P}/locations/{L}/mcpServers/agentregistry-…`.
        tool_name: The tool's `name` as returned by discovery (e.g. `get_stock_quote`).
        arguments: Dict of arguments to pass to the tool. Schemas vary per tool —
                   match the discovery output. Pass `{}` or omit if no args.
    Returns:
        The tool's response (typically a dict). On failure, returns
        `{"error": "..."}` with a human-readable explanation.
    """
    try:
        toolset = _materialize_toolset(mcp_server_name)
    except Exception as e:
        return {"error": f"failed to resolve MCP server '{mcp_server_name}': {e}"}

    try:
        tools = await toolset.get_tools()
    except Exception as e:
        return {"error": f"failed to list tools on MCP server: {e}"}

    target = next((t for t in tools if t.name == tool_name), None)
    if target is None:
        return {
            "error": f"tool '{tool_name}' not found on MCP server",
            "available_tools": [t.name for t in tools],
        }

    try:
        result = await target.run_async(args=arguments or {}, tool_context=tool_context)
        return {"result": result}
    except Exception as e:
        return {"error": f"tool invocation failed: {e}"}


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
