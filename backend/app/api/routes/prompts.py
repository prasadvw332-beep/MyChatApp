"""Prompt A/B test results (app/prompts/registry.py)."""

import asyncio

from fastapi import APIRouter, Query

from app.prompts import registry

router = APIRouter(prefix="/prompts")


@router.get("/experiments")
async def list_prompt_experiments(days: int = Query(30, ge=1, le=365)):
    """Per prompt version: answers, ratings and thumbs-up rate over the last `days` days."""
    return {"split": registry.PROMPT_AB_TEXT, "results": await asyncio.to_thread(registry.get_results, days)}
