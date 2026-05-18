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
    """Flatten the registry MCPServer schema into something the LLM can read."""
    interfaces = server.get("interfaces", []) or []
    url = interfaces[0].get("url", "") if interfaces else ""
    spec = server.get("mcpServerSpec", {}) or {}
    raw_tools = spec.get("toolSpec", {}).get("tools", []) or []
    tools = [{"name": t.get("name"), "description": t.get("description", "")} for t in raw_tools]
    description = server.get("description", "")
    # Attributes come either from a structured field (future-proof) or from `[key:value]`
    # markers in the description (current workaround — see _parse_attributes docstring).
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


def discover_tools_by_intent(intent: str) -> dict[str, Any]:
    """Return MCP servers whose displayName or description contains the intent keyword.

    Args:
        intent: Free-text keyword extracted from the user's question
                (e.g. "sentiment", "portfolio", "cotação", "news").
    Returns:
        Dict with 'criterion', 'query', 'matches' (list of server dicts). Empty
        'matches' means no server matched — the agent should ask the user to rephrase
        or fall back to pre-loaded toolsets.
    """
    q = (intent or "").strip().lower()
    if not q:
        return {"criterion": "intent", "query": intent, "matches": [], "error": "empty intent"}
    matches = [
        s
        for s in _list_all()
        if q in s["display_name"].lower() or q in s["description"].lower()
    ]
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
