"""Request bodies of /evals."""

from pydantic import BaseModel, Field

from evals import run_ragas


class RunRequest(BaseModel):
    metrics: list[str] = Field(default_factory=lambda: list(run_ragas.DEFAULT_METRICS))
    limit: int | None = Field(default=None, ge=1)
