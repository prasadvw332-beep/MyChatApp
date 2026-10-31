# 0011. Cache embeddings by text, and answers to first questions by meaning

- **Status:** accepted
- **Date:** 2026-10-31

## Context
On a CPU, a chat answer takes from one second to over a minute, and the same questions come back:
several people ask about the same uploaded CV or policy. Embeddings are recomputed too: each chat request
embeds its question, and a worker retrying a document, or an edited file uploaded again, re-embeds every
chunk, including those whose text did not change.

The options were:
- **exact-match answer cache** (hash of the question): safe, but "whats the phone number" and "What is the
  phone number?" are different keys, so it rarely hits;
- **semantic answer cache** (nearest stored question by embedding): hits on rewordings, but can return the
  answer to a *different* question that looks alike, and goes stale when the documents change;
- **Redis** for either cache: one more service to run, back up and monitor, when PostgreSQL and Qdrant
  are already there and fast enough for this.

## Decision
- **Embeddings are cached by exact text**: an LRU in each process, then a PostgreSQL table shared by the
  backend and the workers. It cannot be wrong, so it is always on.
- **Answers are cached by meaning, with a strict threshold (0.95)**, in a Qdrant collection next to the
  documents. Measured with nomic-embed-text: rewordings score 0.975–0.997, different questions that share
  most words up to 0.87.
- **Only a chat's first question** is cached. A follow-up depends on the conversation.
- **Every document change empties the answer cache**, and a lookup only accepts answers to questions asked
  after the last clear, so an answer written from the old documents during an upload is never served.
- Only answers that **finished and passed the output guardrails** are stored; the question guardrails still
  run on a hit.
- Both caches **fail open**: a cache error is counted and logged, and the request goes ahead without it.

## Consequences
- A repeated first question is answered in tens of milliseconds instead of seconds to minutes, with no tokens
  spent; re-indexing only embeds changed chunks.
- Answers are shared between users, like the documents. That is correct while answers depend only on the
  documents and the question; personalising the prompt per user would require a per-user cache key.
- An upload empties the whole answer cache, even for questions it does not affect. Tracking which answers
  used which document would keep more, at the cost of a much harder correctness argument.
- Some rewordings miss (one scored 0.926), and a cached answer does not vary the way a sampled one would.
  Both are accepted in exchange for never answering the wrong question.
- If clearing fails after a document change, stale answers can be served until they expire (24 h). That is
  logged as an error and shown on the dashboard.
- Agent mode is not cached: its answers depend on tool calls and external MCP servers.
