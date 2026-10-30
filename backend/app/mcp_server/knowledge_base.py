"""The knowledge base as an MCP server, so any MCP client can search and read the uploaded documents.

The chat agent (app/agents/) talks to it in-process; Claude Desktop, Claude Code, Cursor etc. can
run it over stdio with `python -m app.mcp_server` (see README). Read-only on purpose: uploading and
deleting stay behind the logged-in HTTP API.
"""

import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from app.rag import knowledge_base
from app.rag.chunking import chunk
from app.services import storage

MAX_RESULTS = 10
MAX_READ_CHARS = 8000  # per read_document call; a small model's context fills up fast

server = FastMCP(
    "knowledge-base",
    instructions="Search and read the documents the user uploaded to My Chatbot.",
)
# it logs every request at INFO ("Processing request of type CallToolRequest")
logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)


@server.tool()
async def search_documents(query: str, k: int = 5) -> dict:
    """Find passages in the uploaded documents that are relevant to `query`.

    Returns up to `k` passages with their file name and page. An empty list means the
    documents don't cover the query; try different words or answer without them.
    """
    docs = await asyncio.to_thread(knowledge_base.retrieve, query, max(1, min(k, MAX_RESULTS)))
    return {
        "results": [
            {"source": d.metadata.get("source"), "page": d.metadata.get("page"),
             "doc_id": d.metadata.get("doc_id"), "text": d.page_content}
            for d in docs
        ]
    }


@server.tool()
async def list_documents() -> dict:
    """List the uploaded documents with their id, file name, status and size in chunks."""
    docs = await asyncio.to_thread(knowledge_base.list_documents)
    return {"documents": [{k: d[k] for k in ("doc_id", "filename", "status", "chunks")} for d in docs]}


@server.tool()
async def read_document(doc_id: str, offset: int = 0) -> dict:
    """Read the full text of one document (get the id from list_documents), e.g. to summarize it.

    Long documents come back in parts: if `next_offset` is set, call again with that offset.
    """
    text, filename = await asyncio.to_thread(_document_text, doc_id)
    offset = max(0, offset)
    end = offset + MAX_READ_CHARS
    return {"filename": filename, "text": text[offset:end], "next_offset": end if end < len(text) else None}


def _document_text(doc_id: str) -> tuple[str, str]:
    name = storage.find(doc_id)
    if not name:
        raise ValueError(f"No document with id {doc_id!r}; use list_documents to see the ids.")
    data, _ = storage.read(name)
    filename = name.split("/", 1)[1]
    return "\n\n".join(d.page_content for d in chunk(filename, data)), filename
