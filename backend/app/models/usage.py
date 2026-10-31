"""LLM token usage: one row per model call (chat answer, agent run, chat title)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class LlmUsage(Base):
    """Kept per user here, not in Prometheus: a user id as a metric label would be one time series per
    user, which Prometheus is not built for. Grafana reads this table for the per-user view."""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    # SET NULL, not CASCADE: deleting an account must not make past spending disappear
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    endpoint: Mapped[str] = mapped_column(String(16))  # chat | agent | title
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))  # at the prices configured at the time
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
