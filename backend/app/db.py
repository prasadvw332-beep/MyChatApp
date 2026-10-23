"""PostgreSQL connection (SQLAlchemy + psycopg) for app data such as RAGAS eval results."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create any missing tables."""
    from app import models  # noqa: F401  (registers the tables on Base)

    Base.metadata.create_all(engine)
