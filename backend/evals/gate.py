"""Quality gate: fail (exit 1) when answer quality dropped below the accepted baseline.

    uv run python -m evals.run_ragas --json-out /tmp/eval.json
    uv run python -m evals.gate /tmp/eval.json                    # compare with evals/baseline.json
    uv run python -m evals.gate /tmp/eval.json --update-baseline  # accept these scores as the new baseline

Unit tests check that code does what it says; they can't tell that a new prompt, chunk size or model
made answers worse. This can: CI runs the eval on every change to the RAG code (.github/workflows/
evals.yml) and blocks the merge when a metric falls more than its tolerance below the baseline.

The tolerance matters: LLM scores wobble from run to run, even with temperature 0, and a gate that
fails at random gets ignored. Measure the wobble (run the same commit 3-5 times) and set each
tolerance a little above it. Raise the baseline deliberately (--update-baseline, committed with the
change that earned it), never automatically, or quality can drift down a point at a time.
"""

import argparse
import json
import sys
from pathlib import Path

BASELINE = Path(__file__).parent / "baseline.json"
# how far below the baseline a metric may fall before the gate fails
TOLERANCE = {"faithfulness": 0.08, "answer_relevancy": 0.05, "context_precision": 0.05, "context_recall": 0.08}
DEFAULT_TOLERANCE = 0.05


def check(summary: dict, baseline: dict) -> list[str]:
    failures = []
    for metric, base in baseline.items():
        value = summary.get(metric)
        if value is None:
            failures.append(f"{metric}: no score (was {base:.3f})")
        elif value < base - TOLERANCE.get(metric, DEFAULT_TOLERANCE):
            failures.append(f"{metric}: {value:.3f} < baseline {base:.3f} - {TOLERANCE.get(metric, DEFAULT_TOLERANCE)}")
    return failures


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("result", type=Path, help="a run_ragas --json-out file")
    p.add_argument("--update-baseline", action="store_true")
    args = p.parse_args()
    summary = {k: v for k, v in json.loads(args.result.read_text(encoding="utf-8"))["summary"].items() if v is not None}

    if args.update_baseline or not BASELINE.exists():
        BASELINE.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"baseline set: {summary}")
        return
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    for metric in baseline:
        value = summary.get(metric)
        print(f"  {metric:<20} {'n/a' if value is None else f'{value:.3f}'}  (baseline {baseline[metric]:.3f})")
    failures = check(summary, baseline)
    if failures:
        print("\nQUALITY GATE FAILED:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("\nquality gate passed")


if __name__ == "__main__":
    main()
