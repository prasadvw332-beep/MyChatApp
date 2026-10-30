"""FastAPI dependencies shared by the routes."""

from fastapi import Depends, HTTPException, Request

from app.core.security import COOKIE, user_id_from_token
from app.models import User
from app.services import user_store


def current_user(request: Request) -> User:
    user_id = user_id_from_token(request.cookies.get(COOKIE))
    if user_id is not None and (user := user_store.by_id(user_id)):
        return user
    raise HTTPException(401, "Not logged in.")


CurrentUser = Depends(current_user)
