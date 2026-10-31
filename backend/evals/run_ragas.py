"""Evaluate the RAG pipeline with RAGAS.

Run from backend/ (Ollama and Qdrant must be running, documents already uploaded):

    uv run python -m evals.run_ragas
    uv run python -m evals.run_ragas --limit 3 --metrics faithfulness answer_relevancy

The same run can be started from the app's Evaluation page (POST /evals/run).
Results are saved to the eval_runs table in PostgreSQL (DATABASE_URL), which is where the page reads them from.

Two phases, so Ollama doesn't swap models on every question:
  1. Answer every question with the app's own pipeline (knowledge_base.retrieve + build_messages + llm).
  2. Score the answers with RAGAS, using a local judge model (EVAL_MODEL).

Metrics:
  faithfulness       is every claim in the answer supported by the retrieved chunks?
  answer_relevancy   does the answer address the question?
  context_precision  are the relevant chunks ranked near the top?
  context_recall     do the retrieved chunks contain everything the reference answer needs?
                     (own yes/no-per-sentence version, see evals/metrics.py)
  answer_correctness does the answer match the reference? (opt-in)
"""

import argparse
import asyncio
import json
import math
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.clients.llm import llm
from app.core.config import CHAT_MODEL, EMBED_MODEL, OLLAMA_URL
from app.core.db import SessionLocal, init_db
from app.models import EvalRun
from app.rag import knowledge_base
from app.rag.answer import build_messages
from app.schemas.chat import Message

# Defaults to the chat model so no extra download is needed. A small judge gives rough scores;
# set EVAL_MODEL to a larger model for more trustworthy numbers.
EVAL_MODEL = os.getenv("EVAL_MODEL", CHAT_MODEL)
EVAL_EMBED_MODEL = os.getenv("EVAL_EMBED_MODEL", EMBED_MODEL)

HERE = Path(__file__).parent
DATASET = HERE / "dataset.jsonl"
DEFAULT_METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
ALL_METRICS = DEFAULT_METRICS + ["answer_correctness"]
# metrics that judge the retrieved chunks; meaningless when retrieval returned nothing
NEEDS_CONTEXT = {"faithfulness", "context_precision", "context_recall"}

# progress(phase, done, total): "generating" counts questions, "scoring" counts metric checks
Progress = Callable[[str, int, int], None]


def load_dataset(path: Path = DATASET, limit: int | None = None) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[:limit] if limit else rows


async def generate(rows: list[dict], progress: Progress) -> None:
    """Fill in response + retrieved contexts for each row, exactly as /chat would."""
    for i, row in enumerate(rows, 1):
        docs = await asyncio.to_thread(knowledge_base.retrieve, row["question"])
        messages = build_messages([Message(role="user", content=row["question"])], docs)
        row["response"] = (await llm.ainvoke(messages)).content
        row["contexts"] = [
            {"text": d.page_content, "source": d.metadata.get("source"), "page": d.metadata.get("page")}
            for d in docs
        ]
        progress("generating", i, len(rows))


def build_metrics(names: list[str]) -> dict:
    # imported here so the API can list results without the dev-only ragas dependency
    import instructor
    from openai import AsyncOpenAI
    from ragas.embeddings import embedding_factory
    from ragas.llms.base import InstructorLLM
    from ragas.metrics.collections import (
        AnswerCorrectness,
        AnswerRelevancy,
        ContextPrecision,
        Faithfulness,
    )

    from evals.metrics import SimpleContextRecall

    # Ollama exposes an OpenAI-compatible API, which is what RAGAS' structured-output LLM expects.
    # JSON_SCHEMA mode makes Ollama constrain decoding to the response schema; with ragas'
    # default JSON mode small models often echo the schema back instead of filling it in.
    client = AsyncOpenAI(base_url=f"{OLLAMA_URL}/v1", api_key="ollama")
    judge = InstructorLLM(
        client=instructor.from_openai(client, mode=instructor.Mode.JSON_SCHEMA),
        model=EVAL_MODEL,
        provider="openai",
        max_tokens=512,  # judge outputs are short; a runaway generation fails fast instead of crawling
    )
    embeddings = embedding_factory("openai", model=EVAL_EMBED_MODEL, client=client)
    factories = {
        "faithfulness": lambda: Faithfulness(llm=judge),
        "answer_relevancy": lambda: AnswerRelevancy(llm=judge, embeddings=embeddings),
        "context_precision": lambda: ContextPrecision(llm=judge),
        # RAGAS' ContextRecall needs a larger judge; this one works with the 1B model
        "context_recall": lambda: SimpleContextRecall(EVAL_MODEL, OLLAMA_URL),
        "answer_correctness": lambda: AnswerCorrectness(llm=judge, embeddings=embeddings),
    }
    return {n: factories[n]() for n in names}


async def score_one(name: str, metric, row: dict) -> float:
    q, a, ref = row["question"], row["response"], row["reference"]
    ctx = [c["text"] for c in row["contexts"]]
    if name in NEEDS_CONTEXT and not ctx:
        return math.nan
    match name:
        case "faithfulness":
            result = await metric.ascore(user_input=q, response=a, retrieved_contexts=ctx)
        case "answer_relevancy":
            result = await metric.ascore(user_input=q, response=a)
        case "context_precision":
            result = await metric.ascore(user_input=q, reference=ref, retrieved_contexts=ctx)
        case "context_recall":
            result = await metric.ascore(user_input=q, retrieved_contexts=ctx, reference=ref)
        case "answer_correctness":
            result = await metric.ascore(user_input=q, response=a, reference=ref)
    return float(result.value)


async def score(rows: list[dict], metrics: dict, progress: Progress) -> None:
    total, done = len(rows) * len(metrics), 0
    for row in rows:
        row["scores"], row["errors"] = {}, []
        for name, metric in metrics.items():
            try:
                row["scores"][name] = await score_one(name, metric, row)
            except Exception as e:  # a judge parse failure shouldn't sink the whole run
                row["scores"][name] = math.nan
                row["errors"].append(f"{name}: {type(e).__name__}: {e}"[:400])
            done += 1
            progress("scoring", done, total)


def _mean(values: list[float]) -> float | None:
    ok = [v for v in values if not math.isnan(v)]
    return sum(ok) / len(ok) if ok else None


def _clean(v: float) -> float | None:
    return None if math.isnan(v) else round(v, 4)  # JSON has no NaN


async def run(
    metrics: list[str] = DEFAULT_METRICS,
    limit: int | None = None,
    dataset: Path = DATASET,
    progress: Progress = lambda *_: None,
) -> dict:
    """Run a full evaluation, save it to PostgreSQL and return it."""
    rows = load_dataset(dataset, limit)
    if not rows:
        raise ValueError(f"No questions in {dataset}")
    started = datetime.now()
    t0 = time.perf_counter()
    scorers = build_metrics(metrics)  # fail fast (e.g. ragas missing) before spending time on answers
    await generate(rows, progress)
    await score(rows, scorers, progress)

    result = {
        "id": started.strftime("%Y%m%d_%H%M%S"),
        "created_at": started.isoformat(timespec="seconds"),
        "duration_s": round(time.perf_counter() - t0),
        "chat_model": CHAT_MODEL,
        "judge_model": EVAL_MODEL,
        "metrics": metrics,
        "summary": {m: _mean([r["scores"][m] for r in rows]) for m in metrics},
        "scored": {m: sum(not math.isnan(r["scores"][m]) for r in rows) for m in metrics},
        "no_context": sum(not r["contexts"] for r in rows),
        "rows": [
            {
                "question": r["question"],
                "reference": r["reference"],
                "response": r["response"],
                "contexts": r["contexts"],
                "scores": {m: _clean(v) for m, v in r["scores"].items()},
                "errors": r["errors"],
            }
            for r in rows
        ],
    }
    await asyncio.to_thread(save_run, result)
    return result


def save_run(result: dict) -> None:
    init_db()
    fields = {k: v for k, v in result.items() if k != "created_at"}
    with SessionLocal.begin() as db:
        db.add(EvalRun(**fields, created_at=datetime.fromisoformat(result["created_at"]).astimezone()))


def _to_dict(run: EvalRun) -> dict:
    return {
        "id": run.id,
        "created_at": run.created_at.isoformat(timespec="seconds"),
        "duration_s": run.duration_s,
        "chat_model": run.chat_model,
        "judge_model": run.judge_model,
        "metrics": run.metrics,
        "summary": run.summary,
        "scored": run.scored,
        "no_context": run.no_context,
    }


def list_runs() -> list[dict]:
    """Saved runs, newest first, without the per-question rows."""
    with SessionLocal() as db:
        runs = db.scalars(select(EvalRun).order_by(EvalRun.created_at.desc())).all()
        return [{**_to_dict(r), "questions": len(r.rows)} for r in runs]


def load_run(run_id: str) -> dict | None:
    with SessionLocal() as db:
        run = db.get(EvalRun, run_id)
        return {**_to_dict(run), "rows": run.rows} if run else None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", type=Path, default=DATASET)
    p.add_argument("--metrics", nargs="+", choices=ALL_METRICS, default=DEFAULT_METRICS)
    p.add_argument("--limit", type=int, help="only evaluate the first N questions")
    args = p.parse_args()

    def progress(phase: str, done: int, total: int) -> None:
        print(f"  {phase} {done}/{total}", flush=True)

    result = asyncio.run(run(args.metrics, args.limit, args.dataset, progress))
    for i, r in enumerate(result["rows"], 1):
        shown = "  ".join(f"{m}={'-' if v is None else f'{v:.2f}'}" for m, v in r["scores"].items())
        print(f"[{i}] {shown}  {r['question'][:60]}")
    print("\nSummary (mean over scored rows)")
    for m in result["metrics"]:
        mean = result["summary"][m]
        print(f"  {m:<20} {'n/a' if mean is None else f'{mean:.3f}'}   "
              f"({result['scored'][m]}/{len(result['rows'])} scored)")
    if result["no_context"]:
        print(f"\n  {result['no_context']} question(s) retrieved no chunks (best score < MIN_SCORE); "
              "context metrics are skipped for them.")
    print(f"\nSaved run {result['id']} to PostgreSQL  ({result['duration_s']}s)")


if __name__ == "__main__":
    main()
