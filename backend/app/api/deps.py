"""FastAPI dependencies shared by the routes."""

import asyncio

from fastapi import Depends, HTTPException, Request
from opentelemetry import trace

from app.core import rate_limit
from app.core.logging import set_user_id
from app.core.security import COOKIE, user_id_from_token
from app.models import User
from app.repositories import user_store


async def current_user(request: Request) -> User:
    # async on purpose: FastAPI runs sync dependencies in a worker thread, and the user id set
    # below would stay in that thread instead of reaching the route's (and access log's) log lines
    user_id = user_id_from_token(request.cookies.get(COOKIE))
    if user_id is not None and (user := await asyncio.to_thread(user_store.by_id, user_id)):
        set_user_id(user.id)
        trace.get_current_span().set_attribute("enduser.id", user.id)  # "show me user 9's slow requests"
        return user
    raise HTTPException(401, "Not logged in.")


CurrentUser = Depends(current_user)


def limit_per_user(name: str, spec: str):
    """Route dependency: at most `spec` (e.g. "20/minute") requests per logged-in user, shared by every
    route given the same `name`. See app/core/rate_limit.py."""
    limit = rate_limit.Limit.parse(spec)

    async def dependency(user: User = CurrentUser):  # the same current_user call as the router's: run once
        await rate_limit.check(name, f"user:{user.id}", limit)

    return Depends(dependency)


def limit_per_ip(name: str, spec: str):
    """Like limit_per_user, for routes without a login. The IP is the real client's: uvicorn takes it
    from X-Forwarded-For set by nginx (see backend/Dockerfile)."""
    limit = rate_limit.Limit.parse(spec)

    async def dependency(request: Request):
        await rate_limit.check(name, f"ip:{request.client.host if request.client else '-'}", limit)

    return Depends(dependency)
