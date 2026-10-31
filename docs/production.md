# Production behaviour

What the backend does to be safe and debuggable in production. The settings involved are in [configuration.md](configuration.md); how it is deployed is in [deploy/README.md](../deploy/README.md).

| Concern | How it's handled |
|---|---|
| Configuration | `APP_ENV=production` refuses to start with dev passwords, a short `JWT_SECRET` or `COOKIE_SECURE=false`, and turns off the dev-only localhost CORS rule |
| Errors | Every error is `{"detail", "code", "request_id"}`. Dependency failures return 503 with a generic message; the real exception is only in the logs |
| Tracing | Each request gets an `X-Request-ID` (or keeps the caller's). It is in the response header, the error body and every log line of that request |
| Logs | One JSON object per line in production (for CloudWatch/Loki/ELK), readable text in development |
| Audit | `app.audit` logger: one event per security-relevant action (`auth.login.failure`, `document.delete`, ...) with user id, request id and client IP; see `app/core/audit.py`. Fields named like secrets are redacted |
| Health | `/health` = liveness (process up, never checks dependencies). `/health/ready` = readiness: 503 only if PostgreSQL is down; Qdrant/MinIO/Ollama outages report `degraded` but keep the pod in rotation |
| Timeouts | Ollama (`OLLAMA_TIMEOUT`, connect 5 s), Qdrant (`QDRANT_TIMEOUT`), MinIO (connect 5 s, read 60 s, 3 retries with backoff), PostgreSQL connect 5 s |
| Migrations | Run at startup under a PostgreSQL advisory lock, so several replicas starting together migrate exactly once |
| Metrics | Prometheus metrics on a separate port (`:9100`), never reachable through nginx; see [observability.md](observability.md) |
| Traces | OpenTelemetry: one trace per request and per ingested document, with every SQL query and Ollama/Qdrant call; the trace id is on every log line |
| Token usage | Every model call's tokens and estimated cost, per user, in PostgreSQL and Prometheus; recording never fails a request |
| Restarts | nginx re-resolves the backend's address every 10 s, so a recreated backend doesn't cause 502s |
| Shutdown | Workers finish the current document on SIGTERM; Kubernetes gives them 5 minutes |
