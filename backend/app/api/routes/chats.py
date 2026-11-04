"""Chat history stored in PostgreSQL. The frontend owns chat ids and saves a whole chat per PUT."""

from datetime import datetime, timezone

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.core.audit import audit
from app.core.errors import InvalidInputError, NotFoundError
from app.models import Chat, Message, User
from app.repositories import chat_store, feedback_store
from app.schemas.chats import SaveChatRequest

router = APIRouter(prefix="/chats")


def _to_epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _from_epoch_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _to_response(c: Chat) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "titleBy": c.title_by,
        "titledAt": c.titled_at,
        "updatedAt": _to_epoch_ms(c.updated_at),
        "messages": [
            {k: v for k, v in
             {"role": m.role, "content": m.content, "ui": m.ui, "sources": m.sources, "feedback": m.feedback,
              "at": _to_epoch_ms(m.created_at)}.items()
             if v is not None}
            for m in c.messages
        ],
    }


@router.get("")
def list_chats(user: User = CurrentUser):
    return [_to_response(c) for c in chat_store.list_for_user(user.id)]


@router.put("/{chat_id}")
def save_chat(chat_id: str, body: SaveChatRequest, user: User = CurrentUser):
    if not chat_id or len(chat_id) > 64:
        raise InvalidInputError("Bad chat id.")
    updated = _from_epoch_ms(body.updatedAt)
    messages = [
        Message(position=i, role=m.role, content=m.content, ui=m.ui, sources=m.sources, feedback=m.feedback,
                created_at=_from_epoch_ms(m.at) if m.at else updated)
        for i, m in enumerate(body.messages)
    ]
    try:
        chat_store.save(user.id, chat_id, body.title, body.titleBy, body.titledAt, updated, messages)
    except chat_store.NotChatOwnerError:
        raise NotFoundError("Chat not found.") from None  # 404, not 403: don't confirm the id exists
    return {"ok": True}


@router.delete("/{chat_id}")
def delete_chat(chat_id: str, user: User = CurrentUser):
    chat_store.delete(user.id, chat_id)
    feedback_store.delete(user.id, chat_id)  # the user asked for the chat to be gone: its ratings go too
    audit("chat.delete", chat_id=chat_id)
    return {"ok": True}
