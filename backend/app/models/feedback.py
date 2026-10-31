"""User feedback (thumbs up/down) on answers."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Feedback(Base):
    """One rating per answer, with a copy of what was rated. A copy, not a link to the message: a saved
    chat is rewritten on every change (an edit or a regenerate puts a new answer at the same position),
    so this row must say which answer the user judged. Deleted with its chat (routes/chats.py)."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # no foreign key: the rating can arrive before the chat's save does (the frontend saves in the background)
    chat_id: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)  # the answer's index in the chat
    rating: Mapped[int] = mapped_column(SmallInteger)  # 1 = up, -1 = down
    comment: Mapped[str | None] = mapped_column(Text)  # optional, on a thumbs down
    mode: Mapped[str | None] = mapped_column(String(8))  # chat | agent; None when rated after a page reload
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    sources: Mapped[list | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(String(64))  # the answer's request: its logs and trace
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("user_id", "chat_id", "position", name="uq_feedback_answer"),)
