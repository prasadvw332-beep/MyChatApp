# Evaluation (RAGAS)

[RAGAS](https://docs.ragas.io) scores the pipeline on a set of questions with reference answers in `backend/evals/dataset.jsonl`. Each question is answered with the same retrieval and prompt code as `/chat`, then a local judge model scores:

| Metric | Question it answers |
|---|---|
| `faithfulness` | Is every claim in the answer supported by the retrieved chunks? (hallucination) |
| `answer_relevancy` | Does the answer address the question? |
| `context_precision` | Are the relevant chunks ranked near the top? |
| `context_recall` | Do the retrieved chunks contain everything the reference answer needs? |
| `answer_correctness` | Does the answer match the reference? (opt-in) |

The judge defaults to the chat model (`llama3.2:1b`), so nothing extra needs downloading. Ollama is asked to constrain the judge's output to RAGAS' JSON schema, which keeps a small model's answers parseable. Its scores are still rough (it sometimes marks supported claims as unsupported). RAGAS' own `context_recall` needs a larger judge, so this project uses a small replacement in `backend/evals/metrics.py` that asks one yes/no question per reference sentence. For more trustworthy numbers, pull a larger model and set `EVAL_MODEL` to it.

Open **Evaluation** in the app's sidebar to start a run, follow its progress, and browse past runs. Each question can be expanded to show the answer next to the reference, the retrieved chunks, and any judge errors. On CPU a full run of the 12 questions takes about 10–15 minutes; "Quick" runs only the first 3.

From the command line, with Ollama and Qdrant running and the documents uploaded:

```bash
cd backend
uv run python -m evals.run_ragas                      # all questions, default metrics
uv run python -m evals.run_ragas --limit 3            # quick check
uv run python -m evals.run_ragas --metrics faithfulness answer_correctness
```

It prints per-question scores and a summary. Runs from both the app and the command line are saved as JSON in `backend/evals/results/`. Questions where retrieval returns nothing (best score below `MIN_SCORE`) are skipped for the context metrics and counted in the summary. To add test cases, append lines of `{"question": ..., "reference": ...}` to the dataset. Set `EVAL_MODEL` to use a different judge.
