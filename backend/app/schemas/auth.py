"""Request bodies of /auth/*."""

from pydantic import BaseModel


class CredentialsRequest(BaseModel):
    username: str
    password: str
