"""FastAPI dependencies shared by the routes."""

from fastapi import Depends, HTTPException, Request

from app.core.db import SessionLocal
from app.core.security import COOKIE, user_id_from_token
from app.models import User


def current_user(request: Request) -> User:
    user_id = user_id_from_token(request.cookies.get(COOKIE))
    if user_id is not None:
        with SessionLocal() as db:
            user = db.get(User, user_id)
        if user:
            return user
    raise HTTPException(401, "Not logged in.")


CurrentUser = Depends(current_user)
