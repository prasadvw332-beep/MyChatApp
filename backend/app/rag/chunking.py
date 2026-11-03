"""Text extraction and chunking.

Chunking strategy (structure-aware + contextual headers):
  1. Join all pages into one text, so a section that crosses a page break stays together.
  2. Split the text into sections at headings (Markdown `#` headings or short ALL-CAPS lines
     such as "EDUCATION"; letter-spaced PDF headings like "E D U C AT I O N" are repaired).
  3. Split each section on its own with a recursive splitter (paragraph -> line -> sentence),
     so a chunk never mixes two sections.
  4. Prefix every chunk with "Document: <title> / Section: <heading>" before embedding, so a
     question like "where did he study?" matches the Education chunk even though that word
     isn't in the chunk body.
"""

import bisect
import io
import os
import re

import docx
from docx.table import Table
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from app.core.errors import InvalidInput

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}
MIN_CHUNK = 250  # chars; smaller trailing pieces are merged into the previous chunk

splitter = RecursiveCharacterTextSplitter(
    chunk_size=800, chunk_overlap=100, add_start_index=True
)


def _extract(filename: str, data: bytes) -> list[tuple[int | None, str]]:
    """Return (page_number, text) pairs. page_number is None for plain text."""
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    if ext == ".docx":
        return [(None, _docx_text(data))]
    return [(None, data.decode("utf-8", errors="replace"))]


def _docx_text(data: bytes) -> str:
    """Word -> Markdown-ish text: 'Heading N' styles become '#' headings (so _sections splits on
    them), tables become 'cell | cell' rows. DOCX has no fixed pages, so there are no page numbers."""
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as e:  # not a zip / not a Word file
        raise UnreadableDocument(f"Not a valid .docx file: {e}") from e
    lines: list[str] = []
    for block in document.iter_inner_content():  # paragraphs and tables in document order
        if isinstance(block, Table):
            for row in block.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
            lines.append("")
            continue
        text = block.text.strip()
        if not text:
            continue
        style = block.style.name if block.style is not None else ""
        level = style.removeprefix("Heading ")
        if style == "Title":
            lines.append(f"# {text}")
        elif level.isdigit():
            lines.append(f"{'#' * min(int(level), 6)} {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")


def _unspace(line: str) -> str:
    """Repair letter-spaced PDF headings: 'W O R K  E X P E R I E N C E' -> 'WORK EXPERIENCE'."""
    tokens = line.split()
    if len(tokens) >= 4 and sum(len(t) <= 2 for t in tokens) >= 0.7 * len(tokens):
        return " ".join(w.replace(" ", "") for w in re.split(r"\s{2,}", line.strip()))
    return line


def _heading(line: str) -> tuple[int, str] | None:
    """Return (level, title) if the line looks like a heading."""
    s = line.strip()
    m = _MD_HEADING.match(s)
    if m:
        return len(m.group(1)), m.group(2).strip()
    s = s.rstrip(":").strip()
    if (
        3 <= len(s) <= 60
        and sum(c.isalpha() for c in s) >= 3
        and s.upper() == s
        and len(s.split()) <= 6
        and not s.endswith((".", ",", ";"))
    ):
        return 1, s
    return None


def _sections(text: str) -> list[tuple[str, int, str]]:
    """Split text at headings -> [(heading path, start offset, body)]."""
    sections: list[tuple[str, int, str]] = []
    stack: list[str] = []
    buf: list[str] = []
    start = offset = 0

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append((" > ".join(stack), start, body))

    for raw in text.splitlines(keepends=True):
        line = _unspace(raw.rstrip("\r\n"))
        h = _heading(line)
        if h:
            flush()
            buf = []
            level, title = h
            del stack[level - 1 :]
            stack.append(title)
        else:
            if not buf:
                start = offset
            buf.append(line)
        offset += len(raw)
    flush()
    return sections


class UnreadableDocument(InvalidInput, ValueError):
    """The file has no extractable text. Permanent: retrying won't help."""

    code = "unreadable_document"


def chunk(filename: str, data: bytes) -> list[Document]:
    pages = [(p, t) for p, t in _extract(filename, data) if t.strip()]
    if not pages:
        raise UnreadableDocument("No readable text found in this file (scanned PDFs need OCR).")

    # one continuous text, remembering where each page starts
    full, page_starts, page_nums = "", [], []
    for p, t in pages:
        page_starts.append(len(full))
        page_nums.append(p)
        full += t.rstrip() + "\n"

    title = re.sub(r"[_\-]+", " ", os.path.splitext(filename)[0]).strip()
    docs: list[Document] = []
    for section, start, body in _sections(full):
        pieces = splitter.create_documents([body])
        # fold a tiny last piece into the previous one, so it doesn't become a header-only chunk
        if len(pieces) > 1 and len(pieces[-1].page_content) < MIN_CHUNK:
            tail, prev = pieces.pop().page_content, pieces[-1]
            overlap = next(
                (k for k in range(min(len(prev.page_content), len(tail)), 0, -1)
                 if prev.page_content.endswith(tail[:k])),
                0,
            )
            prev.page_content += ("" if overlap else "\n") + tail[overlap:]
        for piece in pieces:
            pos = start + max(piece.metadata.get("start_index", 0), 0)
            page = page_nums[bisect.bisect_right(page_starts, pos) - 1]
            header = f"Document: {title}" + (f"\nSection: {section}" if section else "")
            meta = {"source": filename, "section": section, "chunk": len(docs)}
            if page:
                meta["page"] = page
            docs.append(Document(page_content=f"{header}\n\n{piece.page_content}", metadata=meta))
    return docs
