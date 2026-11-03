"""Text extraction: file bytes -> [(page number or None, text)], for chunking.py.

Which extractor runs is set by EXTRACTOR:

    basic         pypdf + python-docx (default, light): PDFs with a text layer, DOCX
    docling       Docling, local: layout-aware PDFs (reading order, tables), OCR for scans, plus
                  PPTX, XLSX, HTML and images. Heavy (PyTorch models):  uv add docling
    unstructured  Unstructured, local: the same breadth, element-based (Title, NarrativeText,
                  Table, ...):  uv add "unstructured[all-docs]"  (+ Tesseract and Poppler for OCR)
    azure         Azure AI Document Intelligence, a cloud API (prebuilt-layout): OCR and layout
                  without local models; AZURE_DI_ENDPOINT and AZURE_DI_KEY

Every extractor returns Markdown-ish text, headings as '#' lines, so chunking splits sections the
same way whichever one ran. TXT and MD are always read as they are. The three optional ones are
imported only when chosen, so the default install doesn't need their (large) packages.
"""

import importlib
import os

from app.core.config import EXTRACTOR
from app.core.errors import InvalidInput

Page = tuple[int | None, str]  # (page number, text); None when the format has no fixed pages

EXTRACTORS = {"basic": "basic", "docling": "docling_ext", "unstructured": "unstructured_ext", "azure": "azure_di"}
if EXTRACTOR not in EXTRACTORS:
    raise ValueError(f"EXTRACTOR={EXTRACTOR!r}: use one of {', '.join(EXTRACTORS)}")

BASIC_TYPES = {".pdf", ".docx", ".txt", ".md"}
RICH_TYPES = {".pptx", ".xlsx", ".html", ".png", ".jpg", ".jpeg", ".tiff"}  # docling, unstructured, azure
SUPPORTED = BASIC_TYPES | (RICH_TYPES if EXTRACTOR != "basic" else set())


class UnreadableDocument(InvalidInput, ValueError):
    """The file has no extractable text. Permanent: retrying won't help."""

    code = "unreadable_document"


def extract(filename: str, data: bytes) -> list[Page]:
    if os.path.splitext(filename)[1].lower() in (".txt", ".md"):
        return [(None, data.decode("utf-8", errors="replace"))]
    module = importlib.import_module(f"app.rag.extractors.{EXTRACTORS[EXTRACTOR]}")
    return module.extract(filename, data)
