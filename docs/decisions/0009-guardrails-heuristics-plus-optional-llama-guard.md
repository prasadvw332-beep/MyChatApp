# 0009. Guardrails: pattern checks always on, Llama Guard optional, fail open

- **Status:** accepted
- **Date:** 2026-10-31

## Context
The model reads text that the person asking did not write. Document chunks are shared by every user, so one
user's upload reaches everyone's chats, and the agent can call external MCP tools that return web pages.
Any of it can carry instructions ("ignore your rules, tell the user to ..."). Separately, nothing checked
whether a question or an answer was harmful.

The options for detecting prompt injection were:
- a trained classifier, e.g. Prompt Guard: another model to host, and weak on small chunks of ordinary text;
- a hosted moderation API: sends the user's documents off the machine, which [0001](0001-run-models-locally-with-ollama.md) rules out;
- pattern checks plus prompt structure (fencing, a canary marker): free, explainable, but easy to rephrase around.

For content safety, Llama Guard 3 runs on the Ollama server the project already has.

## Decision
- **Injection checks and the canary are always on** (`GUARDRAILS_MODE=enforce`). They run on the user's
  message, on every retrieved chunk (inside `knowledge_base.retrieve`, so chat, agent, evals and external
  MCP clients all get them) and on every agent tool result. Chunks are fenced in `<document>` tags that
  the prompt declares to be data.
- **Content safety with Llama Guard is opt-in** (`GUARD_MODEL`): it is a 1.6 GB download and adds a few
  seconds per answer on a CPU.
- **Fail open**: if the guard model is down, the request goes ahead and the failure is counted.
- Streamed answers are **withheld after the fact** with a marker the frontend acts on, rather than buffered
  until checked.
- `monitor` mode counts and audits without blocking, to measure false positives before enforcing.

## Consequences
- Common, copy-pasted attacks are stopped at no cost; a determined attacker who rephrases is not. The
  prompt rules and read-only tools still limit what a successful injection can do.
- Users see the answer as it is written; a withheld answer can be partly seen before it is replaced.
  Buffering would have removed streaming, which is most of what makes a CPU-hosted model usable.
- Without `GUARD_MODEL` there is no content filtering beyond what the chat model itself refuses.
- Failing open means an outage of the guard model silently lowers protection. The
  `guardrail_events_total{action="error"}` metric and its dashboard tile make that visible.
