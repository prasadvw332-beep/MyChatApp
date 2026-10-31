# Decision records

Why the project is built the way it is: one short record per significant choice, with the context, the decision and what it costs. New decisions get the next number; a reversed decision is not deleted but marked *superseded* by the new record.

Records 0001 to 0008 were written on 2026-10-31 to document choices already made at the dates shown.

| # | Decision | Date |
|---|---|---|
| 0001 | [Run the models locally with Ollama](0001-run-models-locally-with-ollama.md) | 2026-10-23 |
| 0002 | [PostgreSQL as the ingestion queue](0002-postgresql-as-the-ingestion-queue.md) | 2026-10-31 |
| 0003 | [Agent tools over MCP](0003-agent-tools-over-mcp.md) | 2026-10-31 |
| 0004 | [Metrics on their own port; per-user usage in PostgreSQL](0004-metrics-on-their-own-port-usage-per-user-in-postgresql.md) | 2026-10-31 |
| 0005 | [Login session in an httpOnly cookie](0005-session-cookie-not-tokens-in-the-browser.md) | 2026-10-31 |
| 0006 | [Layered backend: clients, repositories, services](0006-layered-backend.md) | 2026-10-31 |
| 0007 | [Kubernetes: Kustomize base + overlays](0007-kustomize-base-and-overlays.md) | 2026-10-31 |
| 0008 | [Generate the Grafana dashboard from code](0008-generated-grafana-dashboard.md) | 2026-10-31 |
| 0009 | [Guardrails: pattern checks always on, Llama Guard optional, fail open](0009-guardrails-heuristics-plus-optional-llama-guard.md) | 2026-10-31 |
