"""Answer feedback in PostgreSQL (repository: the only module that touches the feedback table)."""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.core.db import SessionLocal
from app.models import Feedback

_KEY = ("user_id", "chat_id", "position")


def put(row: Feedback) -> int | None:
    """Create, or replace the user's earlier rating of the same answer (keeping when it was first given).
    Returns the earlier rating, None if there was none."""
    values = {c.name: getattr(row, c.name) for c in Feedback.__table__.columns if c.name != "id"}
    changed = {k: v for k, v in values.items() if k not in (*_KEY, "created_at")}
    stmt = insert(Feedback).values(**values).on_conflict_do_update(constraint="uq_feedback_answer", set_=changed)
    with SessionLocal.begin() as db:
        before = db.scalar(select(Feedback.rating).where(
            *(getattr(Feedback, k) == getattr(row, k) for k in _KEY)).with_for_update())
        db.execute(stmt)
    return before


def remove(user_id: int, chat_id: str, position: int | None = None) -> None:
    """The rating of one answer, or (position None) of every answer in the chat."""
    stmt = delete(Feedback).where(Feedback.user_id == user_id, Feedback.chat_id == chat_id)
    if position is not None:
        stmt = stmt.where(Feedback.position == position)
    with SessionLocal.begin() as db:
        db.execute(stmt)
