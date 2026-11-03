"""Fine-tune the embedding model on your documents, and measure whether retrieval improved.

    uv add --group finetune sentence-transformers datasets
    uv run python -m finetune.embeddings --data finetune/data.jsonl --base BAAI/bge-small-en-v1.5

A general embedding model knows English, not your company: it doesn't know that "PTO", "annual leave"
and "Urlaub" mean the same here, or that "the Atlas review" is a project gate. Fine-tuning on
(question, chunk that answers it) pairs teaches it, and is cheap: a small model trains in minutes on
one GPU (even on CPU for a few thousand pairs).

Loss: MultipleNegativesRankingLoss. In each batch, every question's own chunk is the positive and the
other questions' chunks are negatives, so it learns "this question is closer to its chunk than to the
others" without labelled negatives. Bigger batches = more negatives = better.

The script holds out 10% of the pairs and reports recall@5 / MRR@10 on them before and after: keep the
new model only if the held-out numbers went up. Serving it: Ollama can't load it directly; run it with
Hugging Face Text Embeddings Inference (TEI) or sentence-transformers behind an endpoint, set the
embedding client to it, and re-index (vectors from different models can't be mixed).
"""

import argparse
import json
import random
from pathlib import Path


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluator(rows: list[dict], name: str):
    from sentence_transformers.evaluation import InformationRetrievalEvaluator
    queries = {str(i): r["question"] for i, r in enumerate(rows)}
    corpus = {r["chunk_id"]: r["chunk"] for r in rows}
    relevant = {str(i): {r["chunk_id"]} for i, r in enumerate(rows)}
    return InformationRetrievalEvaluator(queries, corpus, relevant, name=name, accuracy_at_k=[1, 5], mrr_at_k=[10])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=Path("finetune/data.jsonl"))
    p.add_argument("--base", default="BAAI/bge-small-en-v1.5")
    p.add_argument("--out", default="finetune/embedding-model")
    p.add_argument("--epochs", type=int, default=2)
    p.add_argument("--batch", type=int, default=32)
    args = p.parse_args()

    from datasets import Dataset
    from sentence_transformers import (
        SentenceTransformer,
        SentenceTransformerTrainer,
        SentenceTransformerTrainingArguments,
    )
    from sentence_transformers.losses import MultipleNegativesRankingLoss
    from sentence_transformers.training_args import BatchSamplers

    rows = load(args.data)
    # hold out whole chunks, not just questions: testing on chunks seen in training would flatter the model
    chunk_ids = sorted({r["chunk_id"] for r in rows})
    random.Random(0).shuffle(chunk_ids)
    test_ids = set(chunk_ids[: max(1, len(chunk_ids) // 10)])
    train = [r for r in rows if r["chunk_id"] not in test_ids]
    test = [r for r in rows if r["chunk_id"] in test_ids]

    model = SentenceTransformer(args.base)
    ev = evaluator(test, "held-out")
    print("before:", ev(model))

    trainer = SentenceTransformerTrainer(
        model=model,
        args=SentenceTransformerTrainingArguments(
            output_dir=args.out, num_train_epochs=args.epochs, per_device_train_batch_size=args.batch,
            learning_rate=2e-5, warmup_ratio=0.1,
            batch_sampler=BatchSamplers.NO_DUPLICATES,  # two copies of one chunk in a batch would be false negatives
            save_strategy="no", logging_steps=20,
        ),
        train_dataset=Dataset.from_list([{"anchor": r["question"], "positive": r["chunk"]} for r in train]),
        loss=MultipleNegativesRankingLoss(model),
    )
    trainer.train()
    print("after: ", ev(model))
    model.save(args.out)
    print(f"saved to {args.out}: compare the held-out numbers before switching, then re-index")


if __name__ == "__main__":
    main()
