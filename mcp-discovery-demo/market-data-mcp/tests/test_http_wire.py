"""Smoke test: validate FastMCP Streamable HTTP wire by spawning the server in a subprocess.

This is the critical validation that proves FastMCP 2.x will work behind Cloud Run
(LEARNINGS.md L196 flagged FastMCP 1.x as broken on Cloud Run).
"""

import os
import socket
import subprocess
import sys
import time

import pytest
from fastmcp import Client


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def http_server():
    port = _free_port()
    env = {**os.environ, "PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.main"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            proc.kill()
            raise RuntimeError("server did not start in time")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.asyncio
async def test_http_wire_list_tools(http_server: str):
    async with Client(http_server) as c:
        tools = await c.list_tools()
        assert {t.name for t in tools} == {"get_stock_quote", "get_historical_prices", "get_market_index"}


@pytest.mark.asyncio
async def test_http_wire_call_tool(http_server: str):
    async with Client(http_server) as c:
        r = await c.call_tool("get_stock_quote", {"ticker": "PETR4"})
        assert r.data["ticker"] == "PETR4"
