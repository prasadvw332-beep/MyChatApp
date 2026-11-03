"""Pages of a Confluence Cloud space, saved as Markdown.

    confluence://contoso.atlassian.net/HR      every current page of the space whose key is HR

Setup: create an API token at id.atlassian.com (Security > API tokens) for an account that can
read the space, and set CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN.

Pages aren't files. Their body comes in Confluence's "storage format" (XHTML), which is turned
into Markdown (_html.py): headings stay headings, so chunking splits on them. Each page is
stored as "<page title>.md". Page attachments (a PDF on a page) aren't imported, and macros
keep only their visible text.
"""

import httpx

from app.core.config import CONFLUENCE_API_TOKEN, CONFLUENCE_EMAIL
from app.ingestion.connectors import SourceFile
from app.ingestion.connectors._html import to_markdown


def list_files(url: str) -> list[SourceFile]:
    host, _, space_key = url.removeprefix("confluence://").strip("/").partition("/")
    if not host or not space_key or "/" in space_key:
        raise ValueError(f"expected confluence://<site>.atlassian.net/<SPACE KEY>, got {url!r}")
    if not (CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN):
        raise ValueError("set CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN")
    # Atlassian API tokens use basic auth: the account's email as user name, the token as password
    client = httpx.Client(base_url=f"https://{host}", auth=(CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN), timeout=60)

    def get(path: str, params: dict | None = None) -> dict:
        resp = client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    spaces = get("/wiki/api/v2/spaces", {"keys": space_key})["results"]
    if not spaces:
        raise ValueError(f"space not found (or no access): {space_key}")

    files: list[SourceFile] = []
    path: str | None = f"/wiki/api/v2/spaces/{spaces[0]['id']}/pages"
    params: dict | None = {"body-format": "storage", "status": "current", "limit": 250}
    while path:
        page = get(path, params)
        for p in page["results"]:
            # the listing already carries the page bodies, so read() needs no further request
            data = to_markdown(p["body"]["storage"]["value"]).encode("utf-8")
            files.append(SourceFile(f"{p['title'].replace('/', '-')}.md", len(data), lambda data=data: data))
        # the next page is a relative link that already includes the cursor and our parameters
        path, params = page.get("_links", {}).get("next"), None
    return files
