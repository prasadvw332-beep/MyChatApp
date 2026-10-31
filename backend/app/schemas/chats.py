"""Request bodies of /chats (saved chat history). Field names are the frontend's (camelCase)."""

from pydantic import BaseModel, Field


class MessageIn(BaseModel):
    role: str
    content: str
    ui: str | None = None
    sources: list[dict] | None = None
    at: int | None = None  # ms since epoch


class ChatIn(BaseModel):
    title: str = Field(max_length=500)
    titleBy: str | None = None
    titledAt: int | None = None
    updatedAt: int  # ms since epoch
    messages: list[MessageIn]
