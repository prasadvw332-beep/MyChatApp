"""Pages of a website, crawled from a start URL and saved as Markdown.

    https://docs.contoso.com/handbook/      that page and every page below /handbook/ it links to

The crawl stays on the start URL's host and under its path, follows robots.txt, waits a moment
between requests, and stops after WEB_MAX_PAGES pages. Links with a query string (?page=2,
?sort=...) aren't followed: they're the usual crawler trap, endless variations of one page.
Linked PDF and Word files inside that scope are imported as files.

Pages are fetched while listing, because the links to follow are inside them. Each page is
stored as "<host>/<path>.md" (docs.contoso.com/handbook/leave.md).
"""

import time
from collections import deque
from functools import partial
from posixpath import splitext
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import WEB_MAX_PAGES
from app.ingestion.connectors import SourceFile, get
from app.ingestion.connectors._html import convert

USER_AGENT = "MyChatAppIngest/1.0"  # robots.txt rules are per user agent
DELAY_SECONDS = 0.5  # between page requests: crawl politely, not as fast as the site allows
DOCUMENTS = {".pdf", ".docx", ".txt", ".md"}


def _robots(client: httpx.Client, root: str) -> RobotFileParser:
    robots = RobotFileParser()
    try:
        resp = client.get(f"{root}/robots.txt")
        # no robots.txt (404) means everything is allowed: parse([]) gives exactly that
        robots.parse(resp.text.splitlines() if resp.status_code == 200 else [])
    except httpx.HTTPError:
        robots.parse([])
    return robots


def _name(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.strip("/") or "index"
    stem, ext = splitext(path)
    if ext.lower() in DOCUMENTS:
        return f"{parts.netloc}/{path}"  # a linked file keeps its own name and type
    return f"{parts.netloc}/{stem if ext.lower() in ('.html', '.htm') else path}.md"


def _download(client: httpx.Client, url: str) -> bytes:
    return get(client, url).content


def list_files(url: str) -> list[SourceFile]:
    start = urldefrag(url)[0]
    root = urlsplit(start)
    # the folder of the start page: /handbook/ stays /handbook/, /handbook/intro.html -> /handbook/
    scope = root.path if root.path.endswith("/") else root.path.rsplit("/", 1)[0] + "/"
    client = httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    robots = _robots(client, f"{root.scheme}://{root.netloc}")

    files: list[SourceFile] = []
    to_visit, seen, fetched = deque([start]), {start}, 0
    while to_visit and fetched < WEB_MAX_PAGES:  # breadth-first: pages near the start come first
        page = to_visit.popleft()
        if not robots.can_fetch(USER_AGENT, page):
            continue
        if fetched:
            time.sleep(DELAY_SECONDS)
        fetched += 1
        try:
            resp = client.get(page)
        except httpx.HTTPError:
            continue  # one unreachable page shouldn't stop the crawl
        if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
            continue
        text, links = convert(resp.text)
        data = text.encode("utf-8")
        files.append(SourceFile(_name(page), len(data), lambda data=data: data))

        for href in links:
            link = urldefrag(urljoin(str(resp.url), href))[0]  # relative -> absolute, drop #fragment
            parts = urlsplit(link)
            if (link in seen or parts.netloc != root.netloc or parts.query
                    or parts.scheme not in ("http", "https") or not parts.path.startswith(scope)):
                continue
            seen.add(link)
            if splitext(parts.path)[1].lower() in DOCUMENTS:
                files.append(SourceFile(_name(link), 0, partial(_download, client, link)))
            else:
                to_visit.append(link)
    return files
