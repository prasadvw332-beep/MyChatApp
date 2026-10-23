"""Chat history stored in PostgreSQL. The frontend owns chat ids and saves a whole chat per PUT."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Chat, Message

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
def list_chats():
    with SessionLocal() as db:
        chats = db.scalars(
            select(Chat).options(selectinload(Chat.messages)).order_by(Chat.updated_at.desc())
        ).all()
        return [_out(c) for c in chats]


@router.put("/{chat_id}")
def save_chat(chat_id: str, body: ChatIn):
    if not chat_id or len(chat_id) > 64:
        raise HTTPException(422, "Bad chat id.")
    updated = _dt(body.updatedAt)
    with SessionLocal.begin() as db:
        chat = db.get(Chat, chat_id)
        if chat is None:
            chat = Chat(id=chat_id)
            db.add(chat)
        chat.title, chat.title_by, chat.titled_at, chat.updated_at = (
            body.title, body.titleBy, body.titledAt, updated
        )
        chat.messages = [
            Message(position=i, role=m.role, content=m.content, ui=m.ui, sources=m.sources,
                    created_at=_dt(m.at) if m.at else updated)
            for i, m in enumerate(body.messages)
        ]
    return {"ok": True}


@router.delete("/{chat_id}")
def delete_chat(chat_id: str):
    with SessionLocal.begin() as db:
        chat = db.get(Chat, chat_id)
        if chat:
            db.delete(chat)
    return {"ok": True}
