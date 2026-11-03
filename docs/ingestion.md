# Batch ingestion

Uploading through the UI indexes one file per request. For bulk loads there is a queue with a pool of workers:

```
ingestion.cli (producer) ──► MinIO (files) + PostgreSQL (one row per document, 100 per batch)
                                               │  SELECT ... FOR UPDATE SKIP LOCKED
                              ┌────────────────┼────────────────┐
                           worker 1         worker 2         worker 3      (python -m app.ingestion.worker)
                              └──► extract ─► chunk ─► embed (64 chunks/request) ─► Qdrant
```

- Batches run in order: batch 2 starts when every document in batch 1 is `ready` or `failed`. Inside a batch, whichever worker is free takes the next document.
- A failed document is retried up to 3 times, then marked `failed` with its error. Files without readable text fail at once.
- If a worker dies, its document is taken over by another worker after 10 minutes. Chunk ids are derived from the document id, so a retry never duplicates chunks.
- On shutdown (Ctrl+C, `docker compose down`, Kubernetes scale-down) a worker finishes its current document first.

```bash
cd backend
uv run python -m app.ingestion.worker              # start one worker per terminal (e.g. 3)
uv run python -m app.ingestion.cli ~/my-documents  # queue every PDF/DOCX/TXT/MD under the folder
uv run python -m app.ingestion.cli s3://company-docs/hr   # ...or in an S3 / MinIO bucket (under hr/)
uv run python -m app.ingestion.cli --status        # progress per batch
uv run python -m app.ingestion.cli --failures 4    # why documents in batch 4 failed
uv run python -m app.ingestion.cli --retry-failed
```

The same is available over HTTP: `POST /ingest/batches` (multi-file upload), `GET /ingest/batches`, `GET /ingest/batches/{id}/failures`, `POST /ingest/retry-failed`. In Kubernetes the workers are `deploy/k8s/base/worker-deployment.yaml`, scaled from 2 to 8 replicas on CPU by `deploy/k8s/base/autoscaling.yaml`.

## Sources

The CLI reads from a *connector* (`backend/app/ingestion/connectors/`): each one lists the files of one kind of
source with their size and a way to download them, and the CLI filters and queues them the same way whatever
the source.

| Source | Example | Module |
|---|---|---|
| Local folder | `~/my-documents` | `connectors/local.py` |
| S3 bucket (AWS, MinIO, any S3-compatible store) | `s3://company-docs/hr` | `connectors/s3.py` |
| Google Drive folder (service account; Google Docs exported as .docx) | `gdrive://<folder id>` | `connectors/gdrive.py` |
| SharePoint / Teams document library (Microsoft Graph, app-only sign-in) | `sharepoint://contoso.sharepoint.com/sites/HR/Policies` | `connectors/sharepoint.py` |
| Confluence Cloud space (pages converted to Markdown) | `confluence://contoso.atlassian.net/HR` | `connectors/confluence.py` |
| Azure Blob Storage / ADLS Gen2 container (SAS token) | `azblob://<account>/<container>/hr` | `connectors/azblob.py` |
| Notion page and its sub-pages (blocks converted to Markdown) | `notion://<page id>` | `connectors/notion.py` |
| Jira Cloud project (one file per issue, with comments) | `jira://contoso.atlassian.net/PROJ` | `connectors/jira.py` |
| Slack channel (one file per day, threads included) | `slack://C0123456789` | `connectors/slack.py` |
| Database table (a section per row; any SQLAlchemy database) | `sql://customers`, `sql://sales.orders` | `connectors/sql.py` |
| Website (crawled within the start URL's folder, robots.txt respected) | `https://docs.contoso.com/handbook/` | `connectors/web.py` |

Each module's docstring lists the setup it needs (keys, app registration, permissions). Only the local folder
and S3 (against the local MinIO) have been run; the others are written against the public APIs but not yet
tried with real accounts.

Three kinds of source, three ways to get text:

- **Files** (folder, S3, Azure Blob, Google Drive, SharePoint): download, then the usual extractor (PDF, DOCX, ...).
- **Pages and records from an API** (Confluence, Notion, Jira, Slack, websites): HTML, Notion blocks or
  Atlassian Document Format turned into Markdown, with headings kept so chunking can split on them.
- **Rows** (sql://): each row becomes a small labelled section. Fine for text-heavy tables; numbers and totals
  are better queried live by an agent's SQL tool than searched by similarity.

The S3 connector uses its own credentials (`S3_SOURCE_*`, see [configuration](configuration.md)), because the
source bucket usually belongs to another system and should get a read-only key. Unset, they point at the local
MinIO: to try it, create a bucket in the MinIO console (http://localhost:9001), upload some files, and import
`s3://<bucket>`. Names are stored relative to the prefix (`s3://company-docs/hr` → `policy.pdf`, `2026/leave.docx`).

Not covered yet: syncing (an import is a one-off; a re-run skips unchanged files by content hash but still
downloads them, and files deleted at the source stay indexed) and per-document permissions.

**Word files**: `.docx` headings (`Title`, `Heading 1`–`6` styles) become sections, like Markdown headings,
and tables become `cell | cell` rows in document order. Word has no fixed pages, so DOCX chunks have no page
number. The older binary `.doc` format isn't supported; save it as `.docx` first.

The throughput limit is embedding, not the number of workers: all workers share one Ollama. `OLLAMA_NUM_PARALLEL=3` lets it serve them at the same time. Beyond that, more speed needs more Ollama replicas or a GPU.
