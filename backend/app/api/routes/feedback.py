"""Thumbs up/down on answers: what users think of the answers, next to what the metrics say about them."""

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.core import metrics
from app.models import Feedback, User
from app.repositories import feedback_store
from app.schemas.feedback import FeedbackIn

router = APIRouter(prefix="/feedback")


@router.put("")
async def put_feedback(body: FeedbackIn, user: User = CurrentUser):
    if body.rating is None:
        await asyncio.to_thread(feedback_store.remove, user.id, body.chatId, body.position)
        return {"ok": True}
    now = datetime.now(UTC)
    row = Feedback(
        user_id=user.id, chat_id=body.chatId, position=body.position, rating=1 if body.rating == "up" else -1,
        comment=(body.comment or "").strip() or None, mode=body.mode, question=body.question, answer=body.answer,
        sources=body.sources, request_id=body.requestId, created_at=now, updated_at=now,
    )
    before = await asyncio.to_thread(feedback_store.put, row)
    if before != row.rating:  # not when only a comment was added
        metrics.answer_feedback.labels(body.mode or "unknown", body.rating).inc()
    return {"ok": True}
