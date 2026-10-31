"""Request bodies of /documents."""

from pydantic import BaseModel, Field


class TagsIn(BaseModel):
    tags: list[str] = Field(max_length=20)
