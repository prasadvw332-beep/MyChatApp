"""Which features actually make answers better? Run the same eval once per configuration and compare.

    uv run python -m evals.compare                      # the variants below, first 10 questions
    uv run python -m evals.compare --limit 0 --only baseline hybrid+rerank

Every feature costs something: a rewrite adds a model call before the first token, a reranker adds
latency, hybrid search needs a re-index. Turn one on only when the numbers say it helps *your*
documents and questions. This runs evals/run_ragas.py once per variant, in a separate process with
that variant's settings (settings are read at import, so each run needs a fresh process), then prints
one table.

Read the table with care:
  - a small judge model (the default) gives noisy scores; differences of a few points mean nothing.
    Set EVAL_MODEL to a larger model, and use the full dataset (--limit 0) before deciding.
  - context_precision / context_recall measure retrieval; faithfulness / answer_relevancy the answer.
    A retrieval feature should move the first two; if it doesn't, it isn't working for you.
  - HYBRID_SEARCH reads its own collection: index the documents into it first.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VARIANTS: dict[str, dict[str, str]] = {
    "baseline": {},
    "hybrid": {"HYBRID_SEARCH": "true"},
    "rerank": {"RERANK": "cross_encoder"},
    "hybrid+rerank": {"HYBRID_SEARCH": "true", "RERANK": "cross_encoder"},
    "multi-query": {"QUERY_REWRITE": "multi"},
    "hyde": {"QUERY_REWRITE": "hyde"},
    "prompt-v2": {"PROMPT_AB": "v2=100"},
}


def run_variant(name: str, overrides: dict[str, str], limit: int) -> dict:
    out = Path(tempfile.gettempdir()) / f"eval_{name.replace('+', '_')}_{int(time.time())}.json"
    cmd = [sys.executable, "-m", "evals.run_ragas", "--json-out", str(out)]
    if limit:
        cmd += ["--limit", str(limit)]
    print(f"\n=== {name}  {overrides or '(defaults)'}", flush=True)
    started = time.monotonic()
    subprocess.run(cmd, env={**os.environ, **overrides}, check=True)
    result = json.loads(out.read_text(encoding="utf-8"))
    result["wall_s"] = round(time.monotonic() - started)
    return result


def table(results: dict[str, dict]) -> str:
    metrics = next(iter(results.values()))["metrics"]
    base = results.get("baseline", {}).get("summary", {})
    lines = [f"{'variant':<16}" + "".join(f"{m[:18]:>20}" for m in metrics) + f"{'time':>8}"]
    for name, r in results.items():
        cells = []
        for m in metrics:
            v = r["summary"].get(m)
            delta = "" if name == "baseline" or v is None or base.get(m) is None else f" ({v - base[m]:+.2f})"
            cells.append(f"{'n/a' if v is None else f'{v:.2f}'}{delta}".rjust(20))
        lines.append(f"{name:<16}" + "".join(cells) + f"{r['wall_s']:>7}s")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", nargs="+", choices=list(VARIANTS), help="run just these variants")
    p.add_argument("--limit", type=int, default=10, help="questions per variant (0 = all)")
    args = p.parse_args()
    names = args.only or list(VARIANTS)
    results = {name: run_variant(name, VARIANTS[name], args.limit) for name in names}
    print("\n" + table(results))


if __name__ == "__main__":
    main()
