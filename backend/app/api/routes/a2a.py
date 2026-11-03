"""Agent-to-agent (A2A): other companies' (or teams') agents can call our agent, and we describe it to them.

MCP connects an agent to *tools*. A2A (the Agent2Agent protocol, now under the Linux Foundation)
connects *agents* to each other: a travel-booking agent asks our HR agent "how many leave days does
Asha have left?" without either knowing how the other works inside. Two parts:

    GET  /.well-known/agent-card.json   the Agent Card: who we are, what we can do (skills), where to
                                        call us and how to authenticate. Clients discover us with it.
    POST /a2a                           JSON-RPC 2.0. "message/send": a message in, our agent's reply out.

The spec also has streaming (message/stream, server-sent events), long-running tasks with states
(submitted -> working -> completed) and push notifications; this is the minimal synchronous subset.
Authenticated with a bearer token (A2A_TOKEN), declared in the card. Callers aren't our users, so with
document permissions on they see public documents only (the agent's knowledge-base tools do that).
Off while A2A_TOKEN is unset.
"""

import hmac
import logging
import uuid

from fastapi import APIRouter, Header, HTTPException, Request

from app.agents import loop
from app.core.audit import audit
from app.core.config import A2A_TOKEN, OIDC_REDIRECT_URI
from app.schemas.chat import MAX_MESSAGE_CHARS, Message

router = APIRouter()
log = logging.getLogger(__name__)


def _base_url() -> str:
    return OIDC_REDIRECT_URI.split("/auth/")[0]  # the app's public API root, e.g. https://chat.contoso.com/api


@router.get("/.well-known/agent-card.json")
def agent_card():
    if not A2A_TOKEN:
        raise HTTPException(404, "Not Found")
    return {
        "protocolVersion": "0.3.0",
        "name": "MyChatApp knowledge agent",
        "description": "Answers questions from the company's documents (policies, handbooks, reports), with sources.",
        "url": f"{_base_url()}/a2a",
        "preferredTransport": "JSONRPC",
        "version": "1.0.0",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "security": [{"bearer": []}],
        "skills": [{
            "id": "document-qa",
            "name": "Company document Q&A",
            "description": "Finds and answers from the uploaded company documents, citing the files used.",
            "tags": ["rag", "documents", "policies"],
            "examples": ["How many days of annual leave do employees get?", "Summarize the travel policy."],
        }],
    }


def _error(rpc_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


@router.post("/a2a")
async def a2a(request: Request, authorization: str = Header("")):
    if not A2A_TOKEN:
        raise HTTPException(404, "Not Found")
    if not hmac.compare_digest(authorization.encode(), f"Bearer {A2A_TOKEN}".encode()):
        raise HTTPException(401, "Bad token.")
    body = await request.json()
    rpc_id = body.get("id")
    if body.get("jsonrpc") != "2.0" or body.get("method") != "message/send":
        return _error(rpc_id, -32601, "Method not found: only message/send is supported")  # JSON-RPC codes
    message = (body.get("params") or {}).get("message") or {}
    text = "".join(p.get("text", "") for p in message.get("parts", []) if p.get("kind") == "text").strip()
    if not text or len(text) > MAX_MESSAGE_CHARS:
        return _error(rpc_id, -32602, "Invalid params: a text part of up to 50,000 characters is required")

    tools = getattr(request.app.state, "agent_tools", None) or []
    answer, sources = [], []
    async for event in loop.run([Message(role="user", content=text)], tools):
        if event["type"] == "token":
            answer.append(event["text"])
        elif event["type"] == "sources":
            sources = event["sources"]
    audit("a2a.message", chars=len(text), sources=len(sources))
    return {"jsonrpc": "2.0", "id": rpc_id, "result": {
        "kind": "message",
        "role": "agent",
        "messageId": uuid.uuid4().hex,
        "contextId": message.get("contextId") or uuid.uuid4().hex,
        "parts": [
            {"kind": "text", "text": "".join(answer)},
            {"kind": "data", "data": {"sources": sources}},  # machine-readable citations for the calling agent
        ],
    }}
