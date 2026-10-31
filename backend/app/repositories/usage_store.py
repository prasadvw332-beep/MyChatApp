"""LLM token usage in PostgreSQL (repository: the only module that writes the llm_usage table)."""

from app.core.db import SessionLocal
from app.models import LlmUsage


def add(row: LlmUsage) -> None:
    with SessionLocal() as db:
        db.add(row)
        db.commit()
