# MyChatApp: local RAG chatbot

A ChatGPT-style chat app that answers questions from your own documents, running entirely on your machine.

- **Frontend:** Vite + vanilla JS (streaming replies, chat history, Markdown, citations, light/dark theme)
- **Backend:** FastAPI + LangChain
- **LLM and embeddings:** Ollama (`llama3.2:1b`, `nomic-embed-text`)
- **Vector store:** Qdrant
- **File storage:** MinIO (S3-compatible)

## How it works

1. Upload a PDF, TXT or MD file. The original is stored in MinIO.
2. The text is extracted (`pypdf`), split into section-aware chunks with a `Document / Section` header, embedded with `nomic-embed-text`, and stored in Qdrant.
3. For each question, the most similar chunks are retrieved. If they're relevant, the model answers from them and the reply shows clickable source chips; otherwise it answers as a normal chat.

## Project structure

```
backend/
  app/
    main.py            FastAPI app, CORS, router registration
    config.py          settings from environment variables
    schemas.py         request models
    prompts.py         system prompts
    api/
      chat.py          POST /chat, POST /title
      documents.py     GET/DELETE /documents, POST /upload, GET /documents/{id}/file
    services/
      llm.py           Ollama chat models and embeddings
      chunking.py      text extraction and section-aware chunking
      rag.py           Qdrant indexing, listing, deletion, retrieval
      storage.py       original files in MinIO
  .env.example
  pyproject.toml
frontend/
  index.html
  src/
    main.js
    style.css
  .env.example
  package.json
```

## Prerequisites

Docker containers for the services:

```bash
docker run -d --name ollama -p 11434:11434 ollama/ollama
docker exec ollama ollama pull llama3.2:1b
docker exec ollama ollama pull nomic-embed-text

docker run -d --name qdrant -p 6335:6333 -p 6336:6334 qdrant/qdrant

docker run -d --name minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin123 \
  -v minio-data:/data pgsty/minio server /data --console-address ":9001"
```

You also need [uv](https://docs.astral.sh/uv/) and Node.js.

## Run

Backend (http://localhost:8000):

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

Frontend (http://localhost:5173):

```bash
cd frontend
npm install
npm run dev
```

## Evaluation (RAGAS)

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

## Configuration

The backend reads these environment variables (defaults shown). To use a file, copy `backend/.env.example` to `backend/.env` and start with `uv run --env-file .env uvicorn app.main:app --reload --port 8000`.

| Variable | Default |
|---|---|
| `OLLAMA_URL` | `http://localhost:11434` |
| `CHAT_MODEL` | `llama3.2:1b` |
| `EMBED_MODEL` | `nomic-embed-text` |
| `QDRANT_URL` | `http://localhost:6335` |
| `QDRANT_COLLECTION` | `documents_v2` |
| `MIN_SCORE` | `0.58` (relevance cut-off for using documents) |
| `MINIO_ENDPOINT` | `localhost:9000` |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | `minioadmin` / `minioadmin123` |
| `MINIO_BUCKET` | `documents` |
| `CORS_ORIGINS` | `*` (comma-separated list) |
| `EVAL_MODEL` | same as `CHAT_MODEL` (RAGAS judge) |
| `EVAL_EMBED_MODEL` | same as `EMBED_MODEL` |

The frontend calls the backend at `VITE_API_BASE` (default `http://localhost:8000`); set it in `frontend/.env.local`.

The MinIO credentials are local-development defaults. Change them before exposing the app anywhere.
