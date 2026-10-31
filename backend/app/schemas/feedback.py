"""Request body of /feedback. Field names are the frontend's (camelCase)."""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.chat import MAX_MESSAGE_CHARS


class FeedbackIn(BaseModel):
    chatId: str = Field(min_length=1, max_length=64)
    position: int = Field(ge=0)
    rating: Literal["up", "down"] | None  # None takes the rating back
    comment: str | None = Field(default=None, max_length=2000)
    mode: Literal["chat", "agent"] | None = None  # only known in the page that streamed the answer
    # what was rated, as the user saw it (models/feedback.py says why it is copied)
    question: str = Field(max_length=MAX_MESSAGE_CHARS)
    answer: str = Field(max_length=MAX_MESSAGE_CHARS)
    sources: list[dict] | None = Field(default=None, max_length=50)
    requestId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$")
