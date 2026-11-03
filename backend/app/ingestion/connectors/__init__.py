"""Where bulk-imported documents come from.

A connector lists the files of one kind of source as SourceFile items; cli.py filters and
queues them the same way whatever the source, so adding a source means adding one module here
with a list_files() function and a line in CONNECTORS.

    ./my-documents                                        local folder         (local.py)
    s3://bucket/optional/prefix                           S3 or MinIO bucket   (s3.py)
    gdrive://<folder id>                                  Google Drive folder  (gdrive.py)
    sharepoint://contoso.sharepoint.com/sites/HR[/path]   SharePoint library   (sharepoint.py)
    confluence://contoso.atlassian.net/<SPACE KEY>        Confluence space     (confluence.py)
    azblob://<account>/<container>[/prefix]               Azure Blob Storage   (azblob.py)
    notion://<page id>                                    Notion page tree     (notion.py)
    jira://contoso.atlassian.net/<PROJECT KEY>            Jira project issues  (jira.py)
    slack://<channel id>                                  Slack channel        (slack.py)
    sql://<table> or sql://<schema>.<table>               database table rows  (sql.py)
    https://docs.contoso.com/handbook/                    website, crawled     (web.py)
"""

import importlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

# URL scheme -> module
CONNECTORS = {
    "s3": "s3", "gdrive": "gdrive", "sharepoint": "sharepoint", "confluence": "confluence", "azblob": "azblob",
    "notion": "notion", "jira": "jira", "slack": "slack", "sql": "sql", "http": "web", "https": "web",
}


@dataclass(frozen=True)
class SourceFile:
    name: str  # path relative to the source root ("hr/policy.pdf"), stored as the document's filename
    size: int  # bytes, known from the listing, so oversized files are skipped without downloading them
    read: Callable[[], bytes]  # fetches the content; called by the upload threads, not while listing


class CachedToken:
    """An OAuth access token for the cloud connectors, fetched on first use and again shortly before
    it expires (they last about an hour; a big import can take longer). The upload threads share it."""

    def __init__(self, fetch: Callable[[], tuple[str, int]]):  # fetch() -> (token, lifetime in seconds)
        self._fetch, self._lock = fetch, threading.Lock()
        self._token, self._expires = "", 0.0

    def headers(self) -> dict[str, str]:
        with self._lock:
            if time.monotonic() > self._expires - 60:
                self._token, lifetime = self._fetch()
                self._expires = time.monotonic() + lifetime
            return {"Authorization": f"Bearer {self._token}"}


def get(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """GET that waits out rate limits: SaaS APIs (Notion, Slack, Jira, ...) answer 429 Too Many
    Requests with a Retry-After header when called too fast, and a big import does call fast."""
    for attempt in range(5):
        resp = client.get(url, **kwargs)
        if resp.status_code != 429:
            break
        time.sleep(float(resp.headers.get("Retry-After", 2**attempt)))
    resp.raise_for_status()
    return resp


def list_files(source: str) -> list[SourceFile]:
    scheme, sep, _ = source.partition("://")
    if not sep:
        scheme = "local"
    elif scheme not in CONNECTORS:
        raise ValueError(f"unknown source {scheme}://, expected one of: {', '.join(CONNECTORS)} or a folder")
    # imported on use, so a plain folder import doesn't load the cloud clients
    module = importlib.import_module(f"app.ingestion.connectors.{CONNECTORS.get(scheme, 'local')}")
    return module.list_files(source)
