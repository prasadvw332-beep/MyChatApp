# 0003. Agent tools over MCP

- **Status:** accepted
- **Date:** 2026-10-31

## Context
Agent mode needs tools (search, list and read documents). They could be plain Python functions bound to
the model, or served over the Model Context Protocol (MCP).

## Decision
Expose the knowledge base as an **MCP server** (`backend/app/mcp_server/`) and have the agent get *all* its
tools from MCP servers (`backend/app/agents/tools.py`): ours in-process, plus optional external ones from
`MCP_SERVERS_FILE`.

## Consequences
- One tool definition serves both our agent and any MCP client (Claude Desktop, Claude Code).
- Adding a capability (web fetch, another database) is configuration, not code.
- Slightly more machinery than plain functions; the MCP server is read-only on purpose, so uploads and
  deletes stay behind the logged-in API.
