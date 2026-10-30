"""PostgreSQL connection (SQLAlchemy + psycopg) for app data such as RAGAS eval results."""

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"connect_timeout": 5})

# any constant; Postgres advisory locks are app-defined numbers
_MIGRATION_LOCK = 724_310_001
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Bring the schema up to date by running the Alembic migrations (backend/migrations).

    Every backend replica runs this at startup. The advisory lock makes them take turns: the first
    one migrates, the others wait, then find nothing left to do. Without it, two pods starting
    together could both run the same migration and one would crash.
    """
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]  # backend/
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    with engine.connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _MIGRATION_LOCK})
        try:
            command.upgrade(cfg, "head")
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _MIGRATION_LOCK})


def ping() -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
