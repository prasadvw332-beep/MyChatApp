"""Chat history in PostgreSQL (repository: the only module that queries the chats/messages tables).

Every function takes the owner's user_id: a chat that belongs to someone else behaves as if it
doesn't exist, so ownership can't be forgotten in a route.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.db import SessionLocal
from app.models import Chat, Message


class NotOwner(Exception):
    """The chat id exists but belongs to another user."""


def list_for(user_id: int) -> list[Chat]:
    """The user's chats with their messages, newest first."""
    with SessionLocal() as db:
        return list(db.scalars(
            select(Chat).where(Chat.user_id == user_id).options(selectinload(Chat.messages))
            .order_by(Chat.updated_at.desc())
        ))


def save(user_id: int, chat_id: str, title: str, title_by: str | None, titled_at: int | None,
         updated_at: datetime, messages: list[Message]) -> None:
    """Create or replace a whole chat (the frontend sends the full chat on every save)."""
    with SessionLocal.begin() as db:
        chat = db.get(Chat, chat_id)
        if chat is None:
            chat = Chat(id=chat_id, user_id=user_id)
            db.add(chat)
        elif chat.user_id != user_id:
            raise NotOwner()
        chat.title, chat.title_by, chat.titled_at, chat.updated_at = title, title_by, titled_at, updated_at
        chat.messages.clear()
        db.flush()  # delete the old rows first, or the new ones clash on (chat_id, position)
        chat.messages = messages


def delete(user_id: int, chat_id: str) -> None:
    with SessionLocal.begin() as db:
        chat = db.get(Chat, chat_id)
        if chat and chat.user_id == user_id:
            db.delete(chat)
