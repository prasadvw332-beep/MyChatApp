"""Accounts."""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)  # stored lower-case
    password_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # e.g. ["hr", "finance"]: set by SSO from the identity provider's groups claim; used by document ACLs
    groups: Mapped[list] = mapped_column(JSONB, default=list)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True)  # the user's id at the SSO provider
