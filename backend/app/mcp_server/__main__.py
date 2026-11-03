"""Run the knowledge-base MCP server.

    python -m app.mcp_server            over stdio: the client starts this process (Claude Desktop, Cursor)
    python -m app.mcp_server --http     over HTTP (streamable HTTP) on :8765, for clients elsewhere

stdio needs no authentication: only the user who started the process can talk to it. Over HTTP anyone
who can reach the port could, so every request must carry "Authorization: Bearer <MCP_HTTP_TOKEN>",
checked before the MCP layer sees it. That's the simple version of MCP auth; the full spec uses
OAuth 2.1: the server points clients to an authorization server, clients get a token for the user
(with PKCE), and the server checks the token's audience and scopes per request. Put a gateway that
does this in front of the server, or use an MCP SDK's OAuth support, before exposing it beyond a
private network.

stdout carries the MCP protocol in stdio mode; FastMCP sends its logs to stderr.
"""

import argparse
import hmac
import sys

from app.core.config import MCP_HTTP_TOKEN
from app.mcp_server.knowledge_base import server


class BearerAuth:
    """ASGI middleware: 401 for any HTTP request without the right bearer token."""

    def __init__(self, app, token: str):
        self.app, self.expected = app, f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            given = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(given, self.expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await self.app(scope, receive, send)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--http", action="store_true", help="serve over HTTP instead of stdio")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    if not args.http:
        server.run()
        return
    if len(MCP_HTTP_TOKEN) < 32:
        sys.exit("--http needs MCP_HTTP_TOKEN set to at least 32 random characters")
    import uvicorn
    uvicorn.run(BearerAuth(server.streamable_http_app(), MCP_HTTP_TOKEN), host="0.0.0.0", port=args.port)


main()
