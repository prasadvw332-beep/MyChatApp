"""Run the knowledge-base MCP server over stdio: python -m app.mcp_server

stdout carries the MCP protocol; FastMCP sends its logs to stderr.
"""

from app.mcp_server.knowledge_base import server

server.run()
