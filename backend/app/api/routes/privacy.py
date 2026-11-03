"""The user's own data: download it, or delete the account (app/services/privacy.py)."""

import asyncio

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser
from app.core import security
from app.core.audit import audit
from app.models import User
from app.services import privacy

router = APIRouter(prefix="/me")


@router.get("/export")
async def export(user: User = CurrentUser):
    """Everything stored about you, as one JSON file (right of access / portability)."""
    data = await asyncio.to_thread(privacy.export, user)
    audit("privacy.export")
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="my-data.json"'})


@router.delete("")
async def delete_account(response: Response, user: User = CurrentUser):
    """Delete your account and personal data (right to erasure). Not reversible."""
    counts = await asyncio.to_thread(privacy.erase, user)
    audit("privacy.erase", **counts)  # no username: the event must not keep what was erased
    response.delete_cookie(security.COOKIE, path="/")
    return {"ok": True, **counts}
