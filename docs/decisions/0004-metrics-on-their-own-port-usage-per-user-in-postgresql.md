# 0004. Metrics on their own port; per-user usage in PostgreSQL

- **Status:** accepted
- **Date:** 2026-10-31

## Context
Prometheus needs a `/metrics` endpoint. nginx forwards every `/api/*` request to the backend, so an
endpoint on the API port would be public. Token usage is also wanted per user.

## Decision
- Serve metrics on **port 9100**, separate from the API (`METRICS_PORT`); nginx never proxies it.
- Keep **per-user** token usage in a PostgreSQL table (`llm_usage`), not as a Prometheus label; Prometheus
  holds totals per endpoint and model. Grafana reads both.

## Consequences
- Nothing internal is exposed through the website; Prometheus reaches pods directly (headless Services).
- A user id label would create one time series per user, which Prometheus is not built for; the table
  scales with users and keeps history for billing-style questions.
- Two data sources in Grafana instead of one.
