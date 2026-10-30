"""FastAPI dependencies shared by the routes."""

import asyncio

from fastapi import Depends, HTTPException, Request

from app.core.logging import set_user_id
from app.core.security import COOKIE, user_id_from_token
from app.models import User
from app.services import user_store


async def current_user(request: Request) -> User:
    # async on purpose: FastAPI runs sync dependencies in a worker thread, and the user id set
    # below would stay in that thread instead of reaching the route's (and access log's) log lines
    user_id = user_id_from_token(request.cookies.get(COOKIE))
    if user_id is not None and (user := await asyncio.to_thread(user_store.by_id, user_id)):
        set_user_id(user.id)
        return user
    raise HTTPException(401, "Not logged in.")


CurrentUser = Depends(current_user)
