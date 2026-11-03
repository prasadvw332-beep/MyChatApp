"""Issues of a Jira Cloud project, one Markdown file each: summary, details, description, comments.

    jira://contoso.atlassian.net/PROJ        every issue of the project whose key is PROJ

Setup: an API token from id.atlassian.com (Security > API tokens) of an account that can browse
the project, in JIRA_EMAIL and JIRA_API_TOKEN.

Each issue is stored as "<KEY>.md" (PROJ-123.md), not under its summary: the key never changes,
so a re-import replaces the old version even after someone renames the issue. Descriptions and
comments come as Atlassian Document Format, a JSON tree, which adf_to_text() turns into text.
The search returns an issue's most recent comments; very long threads are cut off.
"""

import re

import httpx

from app.core.config import JIRA_API_TOKEN, JIRA_EMAIL
from app.ingestion.connectors import SourceFile, get

FIELDS = "summary,status,issuetype,priority,assignee,created,updated,description,comment"


def adf_to_text(node: dict) -> str:
    """Atlassian Document Format -> Markdown-ish text. ADF is a tree: a "doc" holds blocks
    (paragraph, heading, bulletList, table, ...), which hold inline nodes (text, mention, ...)."""
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji", "inlineCard"):
        attrs = node.get("attrs", {})
        return attrs.get("text") or attrs.get("url", "")
    if kind == "tableRow":
        return " | ".join(adf_to_text(cell).strip() for cell in node.get("content", [])) + "\n"
    inner = "".join(adf_to_text(child) for child in node.get("content", []))
    if kind == "heading":
        # below the issue's own "## Description": its headings start at ###
        return "#" * min(node.get("attrs", {}).get("level", 1) + 2, 6) + f" {inner}\n\n"
    if kind in ("paragraph", "codeBlock", "blockquote", "panel"):
        return inner + "\n\n"
    if kind == "listItem":
        return f"- {inner.strip()}\n"
    if kind in ("bulletList", "orderedList", "table"):
        return inner + "\n"
    return inner  # doc, tableCell, and anything new: just its content


def _name(field: dict | None) -> str:
    return (field or {}).get("name") or (field or {}).get("displayName") or "-"


def issue_markdown(issue: dict) -> str:
    f = issue["fields"]
    lines = [
        f"# {issue['key']}: {f['summary']}",
        f"Type: {_name(f.get('issuetype'))} | Status: {_name(f.get('status'))} | "
        f"Priority: {_name(f.get('priority'))} | Assignee: {_name(f.get('assignee'))} | "
        f"Created: {f['created'][:10]} | Updated: {f['updated'][:10]}",
        "",
    ]
    if f.get("description"):
        lines += ["## Description", adf_to_text(f["description"]).strip(), ""]
    comments = (f.get("comment") or {}).get("comments", [])
    if comments:
        lines.append("## Comments")
        for c in comments:
            lines += [f"{_name(c.get('author'))} ({c['created'][:10]}):", adf_to_text(c["body"]).strip(), ""]
    return "\n".join(lines).strip()


def list_files(url: str) -> list[SourceFile]:
    host, _, project = url.removeprefix("jira://").strip("/").partition("/")
    if not host or not re.fullmatch(r"[A-Z][A-Z0-9_]+", project):  # the key goes into the JQL query
        raise ValueError(f"expected jira://<site>.atlassian.net/<PROJECT KEY>, got {url!r}")
    if not (JIRA_EMAIL and JIRA_API_TOKEN):
        raise ValueError("set JIRA_EMAIL and JIRA_API_TOKEN")
    client = httpx.Client(base_url=f"https://{host}", auth=(JIRA_EMAIL, JIRA_API_TOKEN), timeout=60)

    files: list[SourceFile] = []
    # JQL is Jira's query language, the same as in the issue search box
    params = {"jql": f"project = {project} ORDER BY key", "fields": FIELDS, "maxResults": 100}
    while True:
        page = get(client, "/rest/api/3/search/jql", params=params).json()
        for issue in page["issues"]:
            data = issue_markdown(issue).encode("utf-8")
            files.append(SourceFile(f"{issue['key']}.md", len(data), lambda data=data: data))
        if page.get("isLast", True) or not page.get("nextPageToken"):
            return files
        params["nextPageToken"] = page["nextPageToken"]
