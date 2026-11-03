"""HTML -> Markdown-ish text, for the connectors whose content is HTML (Confluence pages, websites).

Headings stay headings (so chunking splits on them), list items become "- item", table rows
become "cell | cell". Scripts, styles, navigation and footers are dropped: they'd be in every
page of a site and drown the real content.
"""

import re
from html.parser import HTMLParser

HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# never content. ac:parameter is a Confluence macro's settings
SKIP = {"head", "title", "script", "style", "noscript", "template", "svg", "nav", "footer", "ac:parameter"}


class _Markdown(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out: list[str] = []
        self.links: list[str] = []
        self.first_cell = False
        self.hidden = 0  # depth inside SKIP elements

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.hidden += 1
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        elif tag in HEADINGS:
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "tr":
            self.out.append("\n")
            self.first_cell = True
        elif tag in ("td", "th"):
            if not self.first_cell:
                self.out.append(" | ")
            self.first_cell = False
        elif tag in ("p", "div", "br", "table", "section", "article"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.hidden = max(self.hidden - 1, 0)
        elif tag in HEADINGS or tag in ("p", "table", "ul", "ol"):
            self.out.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.out.append(re.sub(r"\s+", " ", data))  # HTML whitespace: any run is one space

    def text(self) -> str:
        lines = [line.strip() for line in "".join(self.out).splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def convert(html: str) -> tuple[str, list[str]]:
    """The text as Markdown, and the href of every link (as written: may be relative)."""
    parser = _Markdown()
    parser.feed(html)
    parser.close()
    return parser.text(), parser.links


def to_markdown(html: str) -> str:
    return convert(html)[0]
