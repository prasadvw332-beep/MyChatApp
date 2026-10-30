import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser
from app.core import security
from app.core.db import SessionLocal
from app.models import User

router = APIRouter(prefix="/auth")

USERNAME = re.compile(r"[a-z0-9_.-]{3,32}")


class Credentials(BaseModel):
    username: str
    password: str


def _public(user: User) -> dict:
    return {"id": user.id, "username": user.username}


def _login(response: Response, user: User) -> dict:
    response.set_cookie(**security.session_cookie(security.make_token(user)))
    return _public(user)


@router.post("/register", status_code=201)
def register(body: Credentials, response: Response):
    username = body.username.strip().lower()
    if not USERNAME.fullmatch(username):
        raise HTTPException(422, "Username must be 3-32 characters: letters, numbers, . _ -")
    if not 8 <= len(body.password) <= 128:
        raise HTTPException(422, "Password must be 8-128 characters.")
    user = User(username=username, password_hash=security.hash_password(body.password),
                created_at=datetime.now(timezone.utc))
    try:
        with SessionLocal.begin() as db:
            db.add(user)
    except IntegrityError:
        raise HTTPException(409, "That username is taken.")
    return _login(response, user)


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    username = body.username.strip().lower()
    key = f"{username}|{request.client.host if request.client else ''}"
    if security.throttled(key):
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.username == username)).first()
    if not security.check_password(user, body.password):
        security.record_failure(key)
        raise HTTPException(401, "Wrong username or password.")
    security.clear_failures(key)
    return _login(response, user)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(security.COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = CurrentUser):
    return _public(user)
