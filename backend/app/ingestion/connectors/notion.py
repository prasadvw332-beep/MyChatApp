"""A Notion page and every page below it, each saved as Markdown.

    notion://<page id>      the 32-character id at the end of the page's URL
                            (notion.so/Team-Handbook-1a2b3c...), with or without dashes

Setup: create an internal integration (notion.so/profile/integrations), set NOTION_TOKEN to its
secret, and on the page: ... menu > Connections > add the integration. It then sees that page and
everything below it, nothing else.

A Notion page isn't a file but a tree of blocks (paragraph, heading, list item, toggle, table,
...), each holding "rich text". Blocks are fetched 100 at a time; a block with children (a toggle,
a nested list) needs its own request. Sub-pages are blocks too ("child_page"): they become their
own files, under their parent's title ("Handbook/Onboarding.md").
"""

import re

import httpx

from app.core.config import NOTION_TOKEN
from app.ingestion.connectors import SourceFile, get

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"  # Notion versions its API by date; pinned so a change can't surprise us

PREFIX = {
    "heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
    "bulleted_list_item": "- ", "numbered_list_item": "1. ", "to_do": "- [ ] ",
    "quote": "> ", "callout": "> ",
}
LIST_TYPES = {"bulleted_list_item", "numbered_list_item", "to_do"}


def page_id(url: str) -> str:
    m = re.search(r"([0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$", url.removeprefix("notion://").strip("/"))
    if not m:
        raise ValueError(f"expected notion://<page id>, got {url!r}")
    return m.group(1)


def _text(rich_text: list[dict]) -> str:
    """Rich text is a list of styled runs (bold, link, mention, ...); plain_text is each run's text."""
    return "".join(run["plain_text"] for run in rich_text)


def _children(client: httpx.Client, block_id: str):
    params: dict = {"page_size": 100}
    while True:
        body = get(client, f"/blocks/{block_id}/children", params=params).json()
        yield from body["results"]
        if not body.get("has_more"):
            return
        params["start_cursor"] = body["next_cursor"]


def _title(client: httpx.Client, pid: str) -> str:
    # a page's title is whichever of its properties has the type "title" (its name varies)
    for prop in get(client, f"/pages/{pid}").json()["properties"].values():
        if prop["type"] == "title":
            return _text(prop["title"]).replace("/", "-") or "Untitled"
    return "Untitled"


def _render(client: httpx.Client, block_id: str, lines: list[str], sub_pages: list[str], depth: int) -> None:
    for block in _children(client, block_id):
        kind = block["type"]
        body = block.get(kind, {})
        if kind == "child_page":
            sub_pages.append(block["id"])
            continue
        if kind == "table_row":
            lines.append(" | ".join(_text(cell) for cell in body["cells"]))
        elif "rich_text" in body:
            prefix = "- [x] " if kind == "to_do" and body.get("checked") else PREFIX.get(kind, "")
            indent = "  " * depth if kind in LIST_TYPES else ""
            lines.append(indent + prefix + _text(body["rich_text"]))
        if block.get("has_children"):  # toggle content, nested list items, table rows
            _render(client, block["id"], lines, sub_pages, depth + 1)


def _page(client: httpx.Client, pid: str, path: str, files: list[SourceFile]) -> None:
    title = _title(client, pid)
    lines: list[str] = []
    sub_pages: list[str] = []
    _render(client, pid, lines, sub_pages, depth=0)
    data = "\n".join(lines).encode("utf-8")
    files.append(SourceFile(f"{path}{title}.md", len(data), lambda data=data: data))
    for sub in sub_pages:
        _page(client, sub, f"{path}{title}/", files)


def list_files(url: str) -> list[SourceFile]:
    if not NOTION_TOKEN:
        raise ValueError("set NOTION_TOKEN to the integration's secret")
    client = httpx.Client(
        base_url=API, timeout=60,
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": VERSION},
    )
    files: list[SourceFile] = []
    _page(client, page_id(url), "", files)  # pages are fetched while listing: their sub-pages are in them
    return files
