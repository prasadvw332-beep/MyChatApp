import re

from fastapi import APIRouter, HTTPException, Request, Response

from app.api.deps import CurrentUser
from app.core import security
from app.core.audit import audit
from app.core.errors import Conflict, InvalidInput
from app.core.logging import set_user_id
from app.models import User
from app.repositories import user_store
from app.schemas.auth import Credentials

router = APIRouter(prefix="/auth")

USERNAME = re.compile(r"[a-z0-9_.-]{3,32}")


def _public(user: User) -> dict:
    return {"id": user.id, "username": user.username}


def _login(response: Response, user: User) -> dict:
    response.set_cookie(**security.session_cookie(security.make_token(user)))
    return _public(user)


@router.post("/register", status_code=201)
def register(body: Credentials, response: Response):
    username = body.username.strip().lower()
    if not USERNAME.fullmatch(username):
        raise InvalidInput("Username must be 3-32 characters: letters, numbers, . _ -")
    if not 8 <= len(body.password) <= 128:
        raise InvalidInput("Password must be 8-128 characters.")
    try:
        user = user_store.create(username, security.hash_password(body.password))
    except user_store.UsernameTaken:
        raise Conflict("That username is taken.") from None
    set_user_id(user.id)
    audit("auth.register", username=username)
    return _login(response, user)


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    username = body.username.strip().lower()
    ip = request.client.host if request.client else "-"
    key = f"{username}|{ip}"
    if security.throttled(key):
        audit("auth.login.throttled", username=username, ip=ip)
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
    user = user_store.by_username(username)
    if not security.check_password(user, body.password):
        security.record_failure(key)
        # the reason stays in the log only: the client gets the same message either way
        audit("auth.login.failure", username=username, ip=ip, reason="no_such_user" if user is None else "bad_password")
        raise HTTPException(401, "Wrong username or password.")
    security.clear_failures(key)
    set_user_id(user.id)
    audit("auth.login.success", username=username, ip=ip)
    return _login(response, user)


@router.post("/logout")
def logout(request: Request, response: Response):
    set_user_id(security.user_id_from_token(request.cookies.get(security.COOKIE)))
    audit("auth.logout")
    response.delete_cookie(security.COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = CurrentUser):
    return _public(user)
