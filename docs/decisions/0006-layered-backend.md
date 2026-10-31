# 0006. Layered backend: clients, repositories, services

- **Status:** accepted
- **Date:** 2026-10-31

## Context
`services/` held two different kinds of code: modules that talk to external servers (Ollama, MinIO,
Qdrant) and modules that query our own tables. Request models were defined inside route files.

## Decision
Split by responsibility: `clients/` (the only code calling external servers), `repositories/` (the only
code querying PostgreSQL), `services/` (logic combining them), `schemas/` (request/response bodies), with
routes kept thin. See [architecture.md](../architecture.md#layers-in-the-backend).

## Consequences
- Replacing a dependency (Qdrant, Ollama, MinIO) or a table touches one module; tests fake one layer.
- The folder tells a newcomer where a change belongs ("where new things go" table).
- More folders for a small codebase; worth it once a second person works on it.
