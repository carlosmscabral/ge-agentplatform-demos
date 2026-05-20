"""Tool registration entrypoint — follows the fastmcp-builder skill pattern."""

from fastmcp import FastMCP

from app.tools.profile import echo, get_my_profile


def register_all(mcp: FastMCP) -> None:
    """Register every MCP tool on the given FastMCP instance."""
    mcp.tool()(get_my_profile)
    mcp.tool()(echo)
