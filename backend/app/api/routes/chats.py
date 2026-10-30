"""Chat history stored in PostgreSQL. The frontend owns chat ids and saves a whole chat per PUT."""

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser
from app.core.errors import InvalidInput, NotFound
from app.models import Chat, Message, User
from app.services import chat_store

router = APIRouter(prefix="/chats")


class MessageIn(BaseModel):
    role: str
    content: str
    ui: str | None = None
    sources: list[dict] | None = None
    at: int | None = None  # ms since epoch


class ChatIn(BaseModel):
    title: str = Field(max_length=500)
    titleBy: str | None = None
    titledAt: int | None = None
    updatedAt: int  # ms since epoch
    messages: list[MessageIn]


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _out(c: Chat) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "titleBy": c.title_by,
        "titledAt": c.titled_at,
        "updatedAt": _ms(c.updated_at),
        "messages": [
            {k: v for k, v in
             {"role": m.role, "content": m.content, "ui": m.ui, "sources": m.sources, "at": _ms(m.created_at)}.items()
             if v is not None}
            for m in c.messages
        ],
    }


@router.get("")
def list_chats(user: User = CurrentUser):
    return [_out(c) for c in chat_store.list_for(user.id)]


@router.put("/{chat_id}")
def save_chat(chat_id: str, body: ChatIn, user: User = CurrentUser):
    if not chat_id or len(chat_id) > 64:
        raise InvalidInput("Bad chat id.")
    updated = _dt(body.updatedAt)
    messages = [
        Message(position=i, role=m.role, content=m.content, ui=m.ui, sources=m.sources,
                created_at=_dt(m.at) if m.at else updated)
        for i, m in enumerate(body.messages)
    ]
    try:
        chat_store.save(user.id, chat_id, body.title, body.titleBy, body.titledAt, updated, messages)
    except chat_store.NotOwner:
        raise NotFound("Chat not found.") from None  # 404, not 403: don't confirm the id exists
    return {"ok": True}


@router.delete("/{chat_id}")
def delete_chat(chat_id: str, user: User = CurrentUser):
    chat_store.delete(user.id, chat_id)
    return {"ok": True}
