"""The agent's tools, all reached over MCP.

- our knowledge-base server (app/mcp_server/), connected in-process: no subprocess, same protocol
- any external MCP servers listed in MCP_SERVERS_FILE (web search, filesystem, GitHub, ...), in
  langchain-mcp-adapters' format, e.g. {"fetch": {"transport": "stdio", "command": "uvx", "args": ["mcp-server-fetch"]}}

Opened once at startup (main.py lifespan) and shared by all requests.
"""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.shared.memory import create_connected_server_and_client_session

from app.core.config import MCP_SERVERS_FILE
from app.mcp_server.knowledge_base import server as kb_server

log = logging.getLogger(__name__)


@asynccontextmanager
async def connect() -> AsyncIterator[list[BaseTool]]:
    """Yield every tool; the knowledge-base session stays open until the block exits."""
    async with create_connected_server_and_client_session(kb_server) as session:
        tools = await load_mcp_tools(session, server_name="knowledge-base")
        yield tools + await _external_tools()


async def _external_tools() -> list[BaseTool]:
    """Tools of the servers in MCP_SERVERS_FILE. A broken server must not stop the app: it is skipped."""
    if not MCP_SERVERS_FILE:
        return []
    try:
        with open(MCP_SERVERS_FILE, encoding="utf-8") as f:
            connections = json.load(f)
        # each tool call opens its own short session, so nothing here has to stay open
        tools = await MultiServerMCPClient(connections).get_tools()
    except Exception:
        log.exception("Could not load the MCP servers in %s; the agent runs without them", MCP_SERVERS_FILE)
        return []
    log.info("Loaded %d tool(s) from MCP servers: %s", len(tools), ", ".join(connections))
    return tools
