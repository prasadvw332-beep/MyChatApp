"""The default extractor: pypdf for PDFs, python-docx for Word. Light and fast, but a PDF without a
text layer (a scan) has nothing to read, and PDF tables come out as loose lines of text."""

import io
import os

import docx
from docx.table import Table
from pypdf import PdfReader

from app.rag.extractors import Page, UnreadableDocument


def extract(filename: str, data: bytes) -> list[Page]:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    if ext == ".docx":
        return [(None, _docx_text(data))]
    raise UnreadableDocument(f"{ext} needs a richer extractor: set EXTRACTOR=docling, unstructured or azure")


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
