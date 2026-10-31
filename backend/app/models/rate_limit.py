"""Rate limit counters: requests per (limit, user or IP) per time window. Safe to empty at any time."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RateLimit(Base):
    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)  # "<limit>:user:<id>" or "<limit>:ip:<address>"
    window_start: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # unix seconds
    hits: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # for the cleanup sweep
