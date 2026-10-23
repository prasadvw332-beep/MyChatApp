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
uv run uvicorn main:app --reload --port 8000
```

Frontend (http://localhost:5173):

```bash
cd frontend
npm install
npm run dev
```

## Configuration

The backend reads these environment variables (defaults in brackets):

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

The MinIO credentials are local-development defaults. Change them before exposing the app anywhere.
