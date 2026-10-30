"""User accounts in PostgreSQL (repository: the only module that queries the users table)."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal
from app.models import User


class UsernameTaken(Exception):
    pass


def create(username: str, password_hash: str) -> User:
    user = User(username=username, password_hash=password_hash, created_at=datetime.now(UTC))
    try:
        with SessionLocal.begin() as db:
            db.add(user)
    except IntegrityError:
        raise UsernameTaken() from None
    return user


def by_username(username: str) -> User | None:
    with SessionLocal() as db:
        return db.scalars(select(User).where(User.username == username)).first()


def by_id(user_id: int) -> User | None:
    with SessionLocal() as db:
        return db.get(User, user_id)
