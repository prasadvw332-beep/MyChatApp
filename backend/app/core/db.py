"""PostgreSQL connection (SQLAlchemy + psycopg) for app data such as RAGAS eval results."""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Bring the schema up to date by running the Alembic migrations (backend/migrations)."""
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]  # backend/
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")
