"""Agent evaluation: did the agent use the right tools, and did it reach the goal?

    uv run python -m evals.agent_eval                 # evals/agent_tasks.jsonl, against the running agent

RAG evals score one answer. An agent takes a path: which tools, in what order, with what arguments,
how many steps. A right answer reached by luck, or after six needless tool calls, is still a bad
agent. Each task in agent_tasks.jsonl says what a good run looks like:

    {"task": "...", "expect_tools": ["search_documents"], "forbid_tools": ["query_database"],
     "answer_contains": ["24 days"], "max_steps": 3}

Scores per task:
    tool_recall     share of expect_tools that were called           (did it look in the right places?)
    tool_precision  share of calls that were expected                 (did it wander?)
    forbidden       a forbid_tools tool was called                    (safety: e.g. no SQL for a policy question)
    steps_ok        tool calls <= max_steps                           (efficiency, and cost)
    goal            every answer_contains string is in the answer     (task success)

Deterministic checks, no judge model: cheap enough to run on every prompt or model change. For open-
ended tasks, add an LLM judge for the goal, like RAGAS does for answers.
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.agents import loop
from app.agents import tools as agent_tools
from app.schemas.chat import ChatMessage

TASKS = Path(__file__).parent / "agent_tasks.jsonl"


async def run_task(task: dict, tools) -> dict:
    calls, answer = [], []
    async for event in loop.run([ChatMessage(role="user", content=task["task"])], tools):
        if event["type"] == "tool":
            calls.append(event["name"])
        elif event["type"] == "token":
            answer.append(event["text"])
    text = "".join(answer)
    expected, forbidden = set(task.get("expect_tools", [])), set(task.get("forbid_tools", []))
    used = set(calls)
    return {
        "task": task["task"],
        "calls": calls,
        "tool_recall": len(expected & used) / len(expected) if expected else 1.0,
        "tool_precision": sum(c in expected for c in calls) / len(calls) if calls and expected else 1.0,
        "forbidden": bool(forbidden & used),
        "steps_ok": len(calls) <= task.get("max_steps", 4),
        "goal": all(s.lower() in text.lower() for s in task.get("answer_contains", [])),
        "answer": text[:300],
    }


async def main_async(path: Path) -> None:
    tasks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    async with agent_tools.connect() as tools:
        results = [await run_task(t, tools) for t in tasks]
    for r in results:
        flags = "".join([" GOAL" if r["goal"] else " miss", " FORBIDDEN" if r["forbidden"] else "",
                         "" if r["steps_ok"] else " too-many-steps"])
        print(f"[{r['tool_recall']:.2f} recall {r['tool_precision']:.2f} prec{flags}] {r['task'][:60]}  -> {r['calls']}")
    n = len(results)
    print(f"\ngoal reached   {sum(r['goal'] for r in results)}/{n}")
    print(f"tool recall    {sum(r['tool_recall'] for r in results) / n:.2f}")
    print(f"tool precision {sum(r['tool_precision'] for r in results) / n:.2f}")
    print(f"forbidden      {sum(r['forbidden'] for r in results)}   too many steps {sum(not r['steps_ok'] for r in results)}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tasks", type=Path, default=TASKS)
    asyncio.run(main_async(p.parse_args().tasks))


if __name__ == "__main__":
    main()
