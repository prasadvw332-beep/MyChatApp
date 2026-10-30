"""RAGAS evaluation results."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EvalRun(Base):
    """One RAGAS evaluation run; `rows` holds the per-question answers, contexts and scores."""

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(15), primary_key=True)  # YYYYMMDD_HHMMSS
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[int] = mapped_column(Integer)
    chat_model: Mapped[str] = mapped_column(String)
    judge_model: Mapped[str] = mapped_column(String)
    metrics: Mapped[list] = mapped_column(JSONB)
    summary: Mapped[dict] = mapped_column(JSONB)
    scored: Mapped[dict] = mapped_column(JSONB)
    no_context: Mapped[int] = mapped_column(Integer)
    rows: Mapped[list] = mapped_column(JSONB)
