# 0012. Central logs: Loki, fed by Alloy from stdout

- **Status:** accepted
- **Date:** 2026-10-31

## Context
Metrics (Prometheus) and traces (Tempo) were in Grafana, but logs were only in each container's output:
`kubectl logs` one pod at a time, gone when the pod is replaced. With 2-6 backend pods and 2-8 workers,
"what did request X do" meant guessing which pod handled it.

## Decision
- **Loki** stores the logs (single binary, local disk, 7 days, like Tempo). It indexes only labels, so it is
  cheap to run and fits the existing Grafana stack; Elasticsearch/OpenSearch would index every word, at
  several times the memory.
- **Grafana Alloy** ships them, reading each container's stdout (Docker socket in compose, Kubernetes API in
  a cluster). The apps keep logging to stdout and don't know about Loki, so the shipper can change without
  touching them, and the dependencies' logs (PostgreSQL, Qdrant, Ollama, nginx) are collected too. Sending
  logs from the app over OTLP was the alternative; it would miss those containers and lose lines when the
  collector is down.
- Labels stay few (`service`, `container`/`pod`, `level`). `request_id`, `trace_id` and `user_id` are
  structured metadata: filterable, not indexed, so they don't create one stream per request.
- In Kubernetes, one Alloy Deployment with a namespaced Role (list pods, read their logs), instead of a
  DaemonSet with access to every node's disk.

## Consequences
- One place to search every pod's logs, linked both ways with traces.
- The backend and workers log JSON wherever Alloy reads them (`LOG_FORMAT=json`); plain `kubectl logs` and
  `docker compose logs` are less readable.
- Search by text scans lines within the chosen labels and time range: fast enough at this size, slow over
  weeks of all services. Querying with a `service` label and a short range keeps it quick.
- One Alloy pod reads through the API server; a large cluster would switch to a DaemonSet reading
  `/var/log/pods`.
