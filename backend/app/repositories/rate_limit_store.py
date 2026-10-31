"""Rate limit counters in PostgreSQL (repository: the only module that touches the rate_limits table)."""

import random
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from app.core.db import SessionLocal
from app.models import RateLimit

_SWEEP_CHANCE = 0.01  # about one hit in a hundred also deletes finished windows, so the table stays small


def hit(key: str, window_start: int, window_seconds: int) -> int:
    """Counts one request in the window and returns the window's count, this request included."""
    expires_at = datetime.fromtimestamp(window_start + window_seconds, UTC)
    stmt = insert(RateLimit).values(key=key, window_start=window_start, hits=1, expires_at=expires_at)
    # one statement, so two replicas counting the same window at once can't lose an increment
    stmt = stmt.on_conflict_do_update(
        index_elements=["key", "window_start"], set_={"hits": RateLimit.hits + 1}
    ).returning(RateLimit.hits)
    with SessionLocal() as db:
        hits = db.execute(stmt).scalar_one()
        if random.random() < _SWEEP_CHANCE:
            db.execute(delete(RateLimit).where(RateLimit.expires_at < datetime.now(UTC)))
        db.commit()
    return hits
