"""Data retention: delete data older than the policy allows.

    uv run python -m app.maintenance.retention              # apply RETENTION_* settings
    uv run python -m app.maintenance.retention --dry-run    # only count what would go

"Keep everything forever" is the default nobody chose. Privacy law (GDPR's storage limitation, Art. 5)
says personal data is kept no longer than needed, and every old chat is one more thing that can leak.
A retention policy says how long, per kind of data, and a scheduled job enforces it:

    RETENTION_CHAT_DAYS   chats untouched for this long (with their messages and feedback)
    RETENTION_LOG_DAYS    LLM usage rows, prompt-run records and feedback (analytics)

Run it daily (a Kubernetes CronJob, or a task in the Airflow DAG). Deletes in batches so a first run
over years of data doesn't lock the tables for minutes. Logs and traces have their own retention in
Loki (7 days) and Tempo; backups expire on their own schedule.
"""

import argparse
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from app.core.config import RETENTION_CHAT_DAYS, RETENTION_LOG_DAYS
from app.core.db import SessionLocal
from app.core.logging import setup_logging
from app.models import Chat, Feedback, LlmUsage, PromptRun

log = logging.getLogger("retention")
BATCH = 1000


def _purge(model, column, days: int, dry_run: bool) -> int:
    """Delete rows of `model` whose `column` is older than `days`, BATCH at a time."""
    if days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    total = 0
    while True:
        with SessionLocal.begin() as db:
            ids = list(db.scalars(select(model.id).where(column < cutoff).limit(BATCH)))
            if dry_run:
                return db.scalar(select(func.count()).select_from(model).where(column < cutoff)) or 0
            if not ids:
                return total
            db.execute(delete(model).where(model.id.in_(ids)))  # chats take their messages along (CASCADE)
            total += len(ids)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    setup_logging()
    counts = {
        "chats": _purge(Chat, Chat.updated_at, RETENTION_CHAT_DAYS, args.dry_run),
        "feedback": _purge(Feedback, Feedback.created_at, RETENTION_LOG_DAYS, args.dry_run),
        "llm_usage": _purge(LlmUsage, LlmUsage.created_at, RETENTION_LOG_DAYS, args.dry_run),
        "prompt_runs": _purge(PromptRun, PromptRun.created_at, RETENTION_LOG_DAYS, args.dry_run),
    }
    log.info("retention %s: %s", "dry run" if args.dry_run else "done", counts)


if __name__ == "__main__":
    main()
