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


def upsert_sso(subject: str, username: str, password_hash: str, groups: list[str]) -> User:
    """The user with this SSO subject, created on first login, with the groups from this login."""
    with SessionLocal.begin() as db:
        user = db.scalars(select(User).where(User.oidc_subject == subject)).first()
        if user is None:
            taken = set(db.scalars(select(User.username).where(User.username.like(f"{username}%"))))
            name, n = username, 1
            while name in taken:  # asha, asha2, asha3, ...
                n += 1
                name = f"{username}{n}"
            user = User(username=name, password_hash=password_hash, created_at=datetime.now(UTC),
                        oidc_subject=subject, groups=groups)
            db.add(user)
        else:
            user.groups = groups  # group membership changes at the provider: refreshed on every login
    return user
