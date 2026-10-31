from typing import Literal

from pydantic import BaseModel, Field

# Bounds against abuse, not normal use: the frontend resends the whole chat every time, so a limit a
# long chat can reach would break that chat for good.
MAX_MESSAGE_CHARS = 50_000
MAX_MESSAGES = 1_000


class Message(BaseModel):
    # no "system": a client must not be able to write the instructions
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(max_length=MAX_MESSAGES)
