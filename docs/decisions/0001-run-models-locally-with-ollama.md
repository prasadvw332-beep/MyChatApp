# 0001. Run the models locally with Ollama

- **Status:** accepted
- **Date:** 2026-10-23

## Context
The chatbot answers questions about personal documents (a CV, notes). Sending them to a hosted model
means the content leaves the machine, costs money per token, and needs an API key.

## Decision
Run the chat model (`llama3.2:1b`) and the embedding model (`nomic-embed-text`) in **Ollama**, locally or
as a pod in the cluster. All model access goes through `backend/app/clients/llm.py`.

## Consequences
- Documents never leave the infrastructure; no per-token bill, no API key.
- Answers are slower (CPU) and a 1B model is weaker, especially at tool calling (agent mode) and as an
  evaluation judge. Bigger models are a setting (`CHAT_MODEL`, `AGENT_MODEL`, `EVAL_MODEL`).
- Switching to a hosted model changes one module; token and cost tracking already exists for that day.
