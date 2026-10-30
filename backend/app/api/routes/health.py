"""Health checks for Docker and Kubernetes (no login needed).

  /health        liveness:  the process is up and serving. Never touches dependencies, otherwise
                 a database outage would make Kubernetes restart every healthy backend pod.
  /health/ready  readiness: can this pod do useful work? Kubernetes only sends traffic to ready pods.

Only PostgreSQL is critical: without it nobody can even log in. If Qdrant, MinIO or Ollama is down
the pod stays ready (login, chat history and plain chat still work) and reports "degraded", so a
single outage doesn't take the whole site offline.
"""

import asyncio
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core import db
from app.services import llm, storage
from app.services.vector_store import store

router = APIRouter()

CRITICAL = {"postgres": db.ping}
OPTIONAL = {"qdrant": store.ping, "minio": storage.ping, "ollama": llm.ping}
CHECK_TIMEOUT = 3  # seconds; a probe must answer faster than Kubernetes' probe timeout


@router.get("/health")
def health():
    return {"status": "ok"}


async def _check(ping) -> dict:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(asyncio.to_thread(ping), CHECK_TIMEOUT)
        result = {"ok": True}
    except TimeoutError:
        result = {"ok": False, "error": "timeout"}
    except Exception as e:  # the type is enough here; details belong in the dependency's own logs
        result = {"ok": False, "error": type(e).__name__}
    return result | {"ms": round((time.perf_counter() - started) * 1000)}


@router.get("/health/ready")
async def ready():
    checks = {**CRITICAL, **OPTIONAL}
    results = dict(zip(checks, await asyncio.gather(*(_check(p) for p in checks.values())), strict=True))
    if not all(results[name]["ok"] for name in CRITICAL):
        status, code = "unavailable", 503
    elif not all(r["ok"] for r in results.values()):
        status, code = "degraded", 200
    else:
        status, code = "ok", 200
    return JSONResponse({"status": status, "checks": results}, status_code=code)
