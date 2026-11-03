import logging
import re
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentUser, limit_per_ip
from app.core import entra, oidc, security
from app.core.audit import audit
from app.core.config import ACL_ENABLED, COOKIE_SECURE, RATE_LIMIT_REGISTER
from app.core.errors import Conflict, InvalidInput
from app.core.logging import set_user_id
from app.models import User
from app.repositories import user_store
from app.schemas.auth import Credentials

router = APIRouter(prefix="/auth")
log = logging.getLogger(__name__)

USERNAME = re.compile(r"[a-z0-9_.-]{3,32}")


def _public(user: User) -> dict:
    return {"id": user.id, "username": user.username, "groups": user.groups or []}


def _login(response: Response, user: User) -> dict:
    response.set_cookie(**security.session_cookie(security.make_token(user)))
    return _public(user)


@router.post("/register", status_code=201, dependencies=[limit_per_ip("register", RATE_LIMIT_REGISTER)])
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


@router.get("/config")
def auth_config():
    """What the login page and the app should offer: an SSO button, a visibility choice on upload."""
    return {"sso": oidc.enabled(), "acl": ACL_ENABLED}


@router.get("/oidc/login")
def oidc_login():
    """Start single sign-on: off to the identity provider's login page (app/core/oidc.py)."""
    if not oidc.enabled():
        raise HTTPException(404, "Single sign-on is not configured.")
    url, flow = oidc.start()
    response = RedirectResponse(url, status_code=302)
    # 10 minutes to log in. Path "/": behind nginx the browser sees /api/auth/..., in development /auth/...
    response.set_cookie(oidc.FLOW_COOKIE, security.sign(flow, minutes=10), max_age=600, httponly=True,
                        samesite="lax", secure=COOKIE_SECURE, path="/")
    return response


@router.get("/oidc/callback")
def oidc_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """The identity provider sends the browser back here with a one-time code."""
    flow = security.unsign(request.cookies.get(oidc.FLOW_COOKIE))
    if error or not code or not flow or not secrets.compare_digest(state, flow.get("state", "")):
        audit("auth.sso.failure", reason=error or "bad_state")
        raise HTTPException(400, "Sign-in failed or expired. Please try again.")
    try:
        claims, access_token = oidc.finish(code, flow)
    except Exception:
        log.exception("SSO token exchange or verification failed")
        audit("auth.sso.failure", reason="token")
        raise HTTPException(400, "Sign-in failed. Please try again.") from None
    try:
        groups = oidc.groups(claims, access_token)
    except Exception:  # e.g. Graph down during an Entra groups overage: log in, but with no group access
        log.exception("could not read the user's groups; continuing without them")
        groups = []
    subject = entra.subject(claims) if entra.enabled() else claims["sub"]
    # an SSO account has no password of its own: a random, never-shown one keeps /auth/login closed to it
    user = user_store.upsert_sso(subject, oidc.username(claims),
                                 security.hash_password(secrets.token_urlsafe(32)), groups)
    set_user_id(user.id)
    audit("auth.sso.success", username=user.username, groups=len(user.groups or []))
    response = RedirectResponse("/", status_code=302)  # back to the app, now logged in
    response.set_cookie(**security.session_cookie(security.make_token(user)))
    response.delete_cookie(oidc.FLOW_COOKIE, path="/")
    return response


@router.post("/logout")
def logout(request: Request, response: Response):
    set_user_id(security.user_id_from_token(request.cookies.get(security.COOKIE)))
    audit("auth.logout")
    response.delete_cookie(security.COOKIE, path="/")
    # SSO: the frontend sends the browser here next, or the provider's session logs the user straight back in
    sso_logout = None
    if oidc.enabled():
        try:
            sso_logout = oidc.logout_url()
        except Exception:
            log.warning("could not build the SSO sign-out URL", exc_info=True)
    return {"ok": True, "sso_logout": sso_logout}


@router.get("/me")
def me(user: User = CurrentUser):
    return _public(user)
