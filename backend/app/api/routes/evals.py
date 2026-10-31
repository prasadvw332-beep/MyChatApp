"""RAGAS evaluation runs: list saved results and start a new run in the background."""

import asyncio
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.core.audit import audit
from app.schemas.evals import RunRequest
from evals import run_ragas

router = APIRouter(prefix="/evals")

# one run at a time; a full run takes several minutes with a 7B judge
_status: dict = {"running": False}
_task: asyncio.Task | None = None


@router.get("")
async def list_runs():
    return {
        "runs": await asyncio.to_thread(run_ragas.list_runs),
        "judge_model": run_ragas.EVAL_MODEL,
        "questions": len(run_ragas.load_dataset()),
        "metrics": run_ragas.ALL_METRICS,
        "default_metrics": run_ragas.DEFAULT_METRICS,
    }


@router.get("/status")
async def status():
    return _status


@router.get("/{run_id}")
async def get_run(run_id: str):
    if not re.fullmatch(r"\d{8}_\d{6}", run_id):
        raise HTTPException(404, "Run not found.")
    run = await asyncio.to_thread(run_ragas.load_run, run_id)
    if not run:
        raise HTTPException(404, "Run not found.")
    return run


@router.post("/run", status_code=202)
async def start_run(req: RunRequest):
    global _task
    if _status.get("running"):
        raise HTTPException(409, "An evaluation is already running.")
    unknown = set(req.metrics) - set(run_ragas.ALL_METRICS)
    if unknown or not req.metrics:
        raise HTTPException(422, f"Choose metrics from: {', '.join(run_ragas.ALL_METRICS)}")

    total = len(run_ragas.load_dataset(limit=req.limit))
    _status.clear()
    _status.update(running=True, phase="starting", done=0, total=total,
                   started_at=datetime.now().isoformat(timespec="seconds"))

    def progress(phase: str, done: int, total: int) -> None:
        _status.update(phase=phase, done=done, total=total)

    async def job():
        try:
            result = await run_ragas.run(req.metrics, req.limit, progress=progress)
            _status.update(running=False, phase="done", run_id=result["id"])
        except ImportError:
            _status.update(running=False, phase="error",
                           error="RAGAS isn't installed. Run `uv sync` in backend/ (it's a dev dependency).")
        except Exception as e:
            _status.update(running=False, phase="error",
                           error=f"{type(e).__name__}: {e} (are Ollama, Qdrant and the judge model '{run_ragas.EVAL_MODEL}' available?)")

    audit("eval.run_started", metrics=req.metrics, limit=req.limit)
    _task = asyncio.create_task(job())  # keep a reference so it isn't garbage-collected
    return _status
