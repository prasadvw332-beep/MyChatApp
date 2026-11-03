"""Nightly import of every document source into the knowledge base.

Airflow only schedules and watches. The work is done by our own code, unchanged:

    Airflow (this DAG, 02:00 every night)
      └─ list_sources              reads the Airflow Variable "ingest_sources"
          └─ import [one per source, in parallel]
               a throwaway container from the backend image runs
               python -m app.ingestion.cli <source> --wait
                 connector lists + downloads files ─► MinIO + queue rows ─► the worker pool indexes them
                 --wait: the task stays running until every document is ready or failed

So a green run means "searchable", not just "queued", and a source that can't be reached
(expired key, deleted bucket) turns its task red and is retried, without stopping the others.
Re-imports are cheap: unchanged files are skipped by content hash.

Sources: Admin -> Variables -> ingest_sources, a JSON list, e.g.
    ["s3://company-docs", "confluence://contoso.atlassian.net/HR", "https://docs.contoso.com/handbook/"]
"""

import os
from datetime import datetime, timedelta

from airflow.providers.docker.operators.docker import DockerOperator
from airflow.sdk import Variable, dag, task

DEFAULT_SOURCES = ["s3://company-docs"]

# What the import container needs: where the queue and the file store are, and the source
# credentials. Handed down from this Airflow container's own environment (see docker-compose.yml);
# empty ones are left out, so the backend's defaults apply.
PASS_THROUGH = [
    "DATABASE_URL", "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY",
    "S3_SOURCE_ENDPOINT", "S3_SOURCE_ACCESS_KEY", "S3_SOURCE_SECRET_KEY", "S3_SOURCE_SECURE",
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "SHAREPOINT_TENANT_ID", "SHAREPOINT_CLIENT_ID", "SHAREPOINT_CLIENT_SECRET",
    "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN",
    "AZURE_STORAGE_SAS_TOKEN", "NOTION_TOKEN", "JIRA_EMAIL", "JIRA_API_TOKEN",
    "SLACK_BOT_TOKEN", "SLACK_HISTORY_DAYS", "SQL_SOURCE_URL", "WEB_MAX_PAGES",
]


@dag(
    schedule="0 2 * * *",  # 02:00 every night, when nobody is chatting and Ollama is free for embedding
    start_date=datetime(2026, 11, 1),
    catchup=False,  # a missed night isn't made up for: the next run imports the current state anyway
    max_active_runs=1,  # never two imports on top of each other
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=10),  # a source that's briefly down gets another chance
        "execution_timeout": timedelta(hours=6),  # a stuck import fails instead of blocking tomorrow's
    },
    tags=["ingestion"],
)
def ingest_sources():
    @task
    def list_sources() -> list[list[str]]:
        """One import command per configured source."""
        sources = Variable.get("ingest_sources", default=DEFAULT_SOURCES, deserialize_json=True)
        # --sync: documents deleted at the source are deleted here too (with its safety limits)
        return [["python", "-m", "app.ingestion.cli", source, "--wait", "--sync"] for source in sources]

    # .partial() holds what every import shares, .expand() makes one task per command at run
    # time ("dynamic task mapping"): add a source to the Variable and tonight it has its own task
    DockerOperator.partial(
        task_id="import",
        map_index_template="{{ task.command[3] }}",  # the UI lists each task by its source, not 0, 1, 2
        image=os.environ["INGEST_IMAGE"],  # the backend image, which contains the CLI and connectors
        network_mode=os.environ["INGEST_NETWORK"],  # the compose network, to reach postgres and minio
        docker_url=os.environ.get("DOCKER_HOST", "unix://var/run/docker.sock"),
        private_environment={k: v for k in PASS_THROUGH if (v := os.environ.get(k))},  # never shown in the UI
        environment={"LOG_FORMAT": "text"},  # Airflow shows the container's output as the task log
        auto_remove="success",  # keep a failed container around for `docker logs`
        mount_tmp_dir=False,  # Docker runs behind a proxy, not on this machine's filesystem
        max_active_tis_per_dagrun=2,  # imports in parallel, but leave the workers some breathing room
    ).expand(command=list_sources())


ingest_sources()
