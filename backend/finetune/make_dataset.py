"""Training data from your own documents: for each chunk, questions it answers (and the answer).

    uv run python -m finetune.make_dataset --out finetune/data.jsonl --per-chunk 2

Both fine-tuning scripts need examples of *your* domain: the embedding model learns which chunk
answers which question (embeddings.py), the chat model learns the answer style (lora.py). Writing
thousands of examples by hand is slow, so an LLM writes them from the indexed chunks ("synthetic
data"), one line per example:

    {"question": "...", "answer": "...", "chunk": "...", "source": "policy.pdf", "chunk_id": "..."}

Quality decides everything here: a strong model (EVAL_MODEL, or a hosted one) writes far better
questions than a 1B model, and you should read a sample before training on it. Keep a share of the
chunks out of training to test on, or the scores will flatter you.
"""

import argparse
import json
import random
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from app.clients.vector_store import META, store
from app.core.config import OLLAMA_URL
from evals.run_ragas import EVAL_MODEL

PROMPT = (
    "Below is a passage from a company document. Write {n} different questions an employee might ask that "
    "this passage answers, each with a short answer taken from the passage. Vary the wording; don't copy "
    'phrases from the passage into the questions. JSON only: [{{"question": "...", "answer": "..."}}]'
)


def chunks(limit: int) -> list[dict]:
    points, _ = store.client.scroll(store.collection, limit=limit, with_payload=True, with_vectors=False)
    return [{"id": str(p.id), "text": p.payload.get("page_content", ""), "source": p.payload.get(META, {}).get("source")}
            for p in points if len(p.payload.get("page_content", "")) > 200]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=Path("finetune/data.jsonl"))
    p.add_argument("--per-chunk", type=int, default=2)
    p.add_argument("--max-chunks", type=int, default=500)
    args = p.parse_args()
    model = ChatOllama(model=EVAL_MODEL, base_url=OLLAMA_URL, temperature=0.7, format="json")
    rows = 0
    with args.out.open("w", encoding="utf-8") as out:
        found = chunks(args.max_chunks)
        for c in random.sample(found, k=len(found)):  # shuffled, so a partial run still covers every document
            reply = model.invoke([SystemMessage(PROMPT.format(n=args.per_chunk)), HumanMessage(c["text"][:3000])])
            try:
                pairs = json.loads(reply.content)
                pairs = pairs if isinstance(pairs, list) else next(iter(pairs.values()))
            except (ValueError, StopIteration, AttributeError):
                continue  # a malformed reply: skip, don't train on garbage
            for pair in pairs[: args.per_chunk]:
                if isinstance(pair, dict) and pair.get("question") and pair.get("answer"):
                    out.write(json.dumps({**pair, "chunk": c["text"], "source": c["source"], "chunk_id": c["id"]}) + "\n")
                    rows += 1
    print(f"wrote {rows} examples to {args.out}")


if __name__ == "__main__":
    main()
