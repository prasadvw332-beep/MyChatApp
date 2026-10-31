# 0010. Agent on LangGraph, without a checkpointer

- **Status:** accepted
- **Date:** 2026-10-31

## Context
The agent was a hand-written `for` loop: call the model, run its tool calls, repeat until it answers or
runs out of steps. That works, but each new behaviour (a human approval step, a planning step, a
second agent) means reworking the loop by hand. LangGraph describes the same thing as a graph of nodes
and edges, and adds checkpointers for pausing, resuming and remembering runs.

## Decision
- The agent is a LangGraph `StateGraph` with two nodes, `agent` (one model call) and `tools` (run the
  calls, screen the results), and the same step limit as before.
- The nodes send their events (tokens, tool steps) through LangGraph's stream writer, so the
  `/agent/chat` stream and the frontend are unchanged.
- The tools node is our own, not LangGraph's prebuilt `ToolNode`, because it also screens results for
  prompt injection, records metrics and spans, and collects the documents to cite.
- **No checkpointer.** A run finishes within one request, and the chat history is stored per user in
  PostgreSQL and sent with each request, so nothing needs to be saved between steps.
- The graph is compiled per request. That takes about a millisecond, and it ties the model, the tools and
  the usage tally to that run.

## Consequences
- New steps are a node and an edge, not a rewrite of the loop.
- `langgraph` is now a direct runtime dependency (it was only pulled in by ragas, for the evals).
- When a feature needs to stop and resume a run (human approval, long tasks) or keep memory across
  chats, add a checkpointer (`langgraph-checkpoint-postgres`) or a store then. Until then it would only
  duplicate the chat history.
