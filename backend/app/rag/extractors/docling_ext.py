"""Docling (github.com/docling-project/docling): local, layout-aware extraction.

Docling runs a layout model and a table-structure model on each page (and OCR where a page has
no text layer), and builds a DoclingDocument: headings, paragraphs, lists and tables in reading
order, each with the page it's on. Exporting it page by page as Markdown keeps the page numbers
for citations, and tables stay real tables instead of loose words.

Install:  uv add docling   (pulls in PyTorch). The first conversion downloads the models
(a few hundred MB); it's CPU-heavy, a GPU makes it much faster.
"""

from functools import cache
from io import BytesIO

from app.rag.extractors import Page, UnreadableDocumentError


@cache
def _converter():
    """One converter per worker process: loading the models takes seconds, so not per document."""
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as e:
        raise RuntimeError("EXTRACTOR=docling needs the package: uv add docling") from e
    return DocumentConverter()


def extract(filename: str, data: bytes) -> list[Page]:
    converter = _converter()  # first: says what to install if Docling is missing
    from docling.datamodel.base_models import DocumentStream
    from docling.exceptions import ConversionError

    try:
        # the name tells Docling the format (.pdf, .pptx, ...); the bytes never touch the disk
        result = converter.convert(DocumentStream(name=filename, stream=BytesIO(data)))
    except ConversionError as e:  # corrupt or unsupported file: permanent, don't retry
        raise UnreadableDocumentError(f"Docling couldn't read this file: {e}") from e
    doc = result.document
    if doc.pages:  # PDFs, images and slides have pages
        return [(n, doc.export_to_markdown(page_no=n)) for n in sorted(doc.pages)]
    return [(None, doc.export_to_markdown())]  # DOCX, HTML, XLSX: one flowing document
