"""Request bodies of /documents."""

from pydantic import BaseModel, Field


class SetTagsRequest(BaseModel):
    tags: list[str] = Field(max_length=20)


class SetAclRequest(BaseModel):
    # "public", "user:<id>" or "group:<name>"; see app/core/acl.py
    acl: list[str] = Field(max_length=50)
