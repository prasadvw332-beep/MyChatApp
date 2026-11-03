"""Long-term memory: facts about a user that the agent keeps across chats (AGENT_MEMORY=true).

Short-term memory is the conversation itself: it arrives with every request and ends with the
chat. Long-term memory outlives it: "I work in the Pune office", "answer in German", "my
manager is Meera". Two halves:

    write   a `remember` tool: the model decides what is worth keeping (only things the user says
            about themselves), one row per fact in user_memories
    read    the user's latest facts go into the system prompt at the start of every agent run,
            so they apply without the model having to look them up

Production systems add: extraction after each chat (instead of relying on the model calling a
tool), de-duplication and updates ("moved to Mumbai" replaces "works in Pune"), semantic search
over many memories instead of "the latest N", and a page where users can see and delete what is
stored about them, which privacy law (GDPR) requires anyway.
"""

from datetime import UTC, datetime

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import UserMemory

MAX_IN_PROMPT = 20
MAX_CHARS = 300


def recent(user_id: int, limit: int = MAX_IN_PROMPT) -> list[str]:
    with SessionLocal() as db:
        rows = db.scalars(select(UserMemory.text).where(UserMemory.user_id == user_id)
                          .order_by(UserMemory.created_at.desc()).limit(limit))
        return list(rows)


def prompt_block(user_id: int) -> str:
    """Text appended to the agent's system prompt; empty when nothing is stored."""
    facts = recent(user_id)
    if not facts:
        return ""
    return "\n\nWhat you know about this user from earlier chats:\n" + "\n".join(f"- {f}" for f in facts)


def tools_for(user_id: int) -> list[BaseTool]:
    """The memory tools of one user: the user id is bound here, never taken from the model's arguments,
    so a prompt can't make the agent write into someone else's memory."""

    @tool
    def remember(fact: str) -> str:
        """Save a lasting fact or preference the user told you about themselves (role, team, location,
        preferred language or style), so you know it in later chats. Not for anything else."""
        text = fact.strip()[:MAX_CHARS]
        if not text:
            return "Nothing to remember."
        with SessionLocal.begin() as db:
            db.add(UserMemory(user_id=user_id, text=text, created_at=datetime.now(UTC)))
        return "Saved."

    return [remember]
