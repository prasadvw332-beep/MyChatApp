"""Prompt versions and A/B tests.

A prompt change is a behaviour change: it can make answers better for one kind of question and
worse for another, and nobody notices until users complain. So prompts get versions, like code,
and a new version is tested on part of the traffic before it replaces the old one:

    PROMPT_AB="v1=50,v2=50"     half the users get v1, half v2

Assignment is by user, not by request (a hash of the user id): one person always sees the same
version, so a chat doesn't change style halfway, and their feedback counts for one side only.
Every answer records its version (prompt_runs, keyed by request id). Feedback carries the same
request id, so the result is one query: thumbs-up rate per version (results(), GET
/prompts/experiments). The evals (RAGAS) are the offline half: run them per version before
exposing users to it at all.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select

from app.core.config import PROMPT_AB
from app.core.db import SessionLocal
from app.models import Feedback, PromptRun
from app.prompts.rag import RAG_PROMPT, RAG_PROMPT_V2

VERSIONS = {"rag": {"v1": RAG_PROMPT, "v2": RAG_PROMPT_V2}}
DEFAULT = "v1"


def _split() -> list[tuple[str, int]]:
    """'v1=50,v2=50' -> [('v1', 50), ('v2', 50)]."""
    parts = [p.split("=") for p in PROMPT_AB.split(",") if "=" in p]
    split = [(name.strip(), int(weight)) for name, weight in parts]
    unknown = [name for name, _ in split if name not in VERSIONS["rag"]]
    if unknown:
        raise ValueError(f"PROMPT_AB names unknown prompt versions: {', '.join(unknown)}")
    return split


SPLIT = _split()
PROMPT_AB_TEXT = PROMPT_AB or f"{DEFAULT}=100"


def choose(prompt: str, user_id: int | None) -> tuple[str, str]:
    """(version, prompt text) for this user. Without PROMPT_AB, always the default."""
    if not SPLIT or user_id is None:
        return DEFAULT, VERSIONS[prompt][DEFAULT]
    bucket = int(hashlib.sha256(f"{prompt}:{user_id}".encode()).hexdigest(), 16) % sum(w for _, w in SPLIT)
    for version, weight in SPLIT:
        if bucket < weight:
            return version, VERSIONS[prompt][version]
        bucket -= weight
    return DEFAULT, VERSIONS[prompt][DEFAULT]


def record(request_id: str, user_id: int | None, prompt: str, version: str) -> None:
    with SessionLocal.begin() as db:
        db.add(PromptRun(request_id=request_id, user_id=user_id, prompt=prompt, version=version,
                         created_at=datetime.now(UTC)))


def results(days: int = 30) -> list[dict]:
    """Per version: answers given, how many were rated, and the share rated up."""
    since = datetime.now(UTC) - timedelta(days=days)
    query = (
        select(
            PromptRun.prompt, PromptRun.version,
            func.count(PromptRun.id).label("answers"),
            func.count(Feedback.id).label("rated"),
            func.sum(case((Feedback.rating == 1, 1), else_=0)).label("up"),
        )
        .outerjoin(Feedback, Feedback.request_id == PromptRun.request_id)
        .where(PromptRun.created_at >= since)
        .group_by(PromptRun.prompt, PromptRun.version)
        .order_by(PromptRun.prompt, PromptRun.version)
    )
    with SessionLocal() as db:
        rows = db.execute(query).all()
    return [
        {"prompt": r.prompt, "version": r.version, "answers": r.answers, "rated": r.rated, "up": r.up or 0,
         # with few ratings the rate is noise: decide on a few hundred per side, not ten
         "up_rate": round((r.up or 0) / r.rated, 3) if r.rated else None}
        for r in rows
    ]
