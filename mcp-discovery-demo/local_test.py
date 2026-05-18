"""Spawn all 3 MCP servers locally and probe each via Streamable HTTP.

This is the local end-to-end wire test before any cloud deploy. Run:

    uv run python local_test.py

Each server is launched in its own subprocess (using its own .venv), bound to a
free port, then a FastMCP Client connects via http://127.0.0.1:<port>/mcp and
calls list_tools + one representative tool. Exits non-zero on any failure.
"""

import asyncio
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from fastmcp import Client

ROOT = Path(__file__).parent

SERVERS = [
    {"name": "market-data", "dir": "market-data-mcp", "probe_tool": "get_stock_quote", "probe_args": {"ticker": "PETR4"}},
    {"name": "portfolio", "dir": "portfolio-mcp", "probe_tool": "get_portfolio_holdings", "probe_args": {"account_id": "account-001"}},
    {"name": "news-sentiment", "dir": "news-sentiment-mcp", "probe_tool": "get_sentiment_score", "probe_args": {"ticker": "AAPL"}},
]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_listening(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"port {port} did not start listening within {timeout}s")


@contextmanager
def spawn(server: dict):
    port = free_port()
    cwd = ROOT / server["dir"]
    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "app.main"],
        cwd=cwd,
        env={**__import__("os").environ, "PORT": str(port)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_listening(port)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


async def probe(server: dict, port: int) -> None:
    url = f"http://127.0.0.1:{port}/mcp"
    async with Client(url) as c:
        tools = await c.list_tools()
        tool_names = sorted(t.name for t in tools)
        print(f"  [{server['name']}] listening on :{port}")
        print(f"  [{server['name']}] tools: {tool_names}")
        r = await c.call_tool(server["probe_tool"], server["probe_args"])
        print(f"  [{server['name']}] {server['probe_tool']}({server['probe_args']}) -> {r.data}")


async def main() -> int:
    print("=" * 70)
    print("mcp-discovery-demo — local multi-server smoke test")
    print("=" * 70)
    failures = 0
    for server in SERVERS:
        print(f"\n>>> {server['name']}")
        try:
            with spawn(server) as port:
                await probe(server, port)
            print(f"  ✓ {server['name']} OK")
        except Exception as e:
            failures += 1
            print(f"  ✗ {server['name']} FAILED: {e}")
    print("\n" + "=" * 70)
    if failures:
        print(f"FAILED — {failures}/{len(SERVERS)} server(s) had errors")
        return 1
    print(f"OK — all {len(SERVERS)} servers passed wire-protocol check")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
