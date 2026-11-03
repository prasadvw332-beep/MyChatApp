"""Unstructured (github.com/Unstructured-IO/unstructured): local, element-based extraction.

partition() detects the file type and splits the file into elements, each with a category
(Title, NarrativeText, ListItem, Table, Header, ...) and metadata (page_number, category_depth,
...). They're turned back into Markdown per page: Title -> '#' heading at its depth, ListItem ->
'- item', Table -> its text. Headers, footers and page numbers repeat on every page and are
dropped: they'd only add noise to every chunk.

strategy="auto" picks per document: "fast" reads the text layer, "hi_res" runs a layout model and
OCR for scans and complex layouts.

Install:  uv add "unstructured[all-docs]"   plus the system tools Tesseract (OCR) and Poppler
(PDF rendering) for hi_res.
"""

from io import BytesIO

from app.rag.extractors import Page, UnreadableDocument

NOISE = {"Header", "Footer", "PageNumber", "PageBreak"}


def _line(element) -> str | None:
    text = (element.text or "").strip()
    kind = element.category
    if not text or kind in NOISE:
        return None
    if kind == "Title":
        depth = getattr(element.metadata, "category_depth", None) or 0
        return "#" * min(depth + 1, 6) + f" {text}"
    if kind == "ListItem":
        return f"- {text}"
    return text  # NarrativeText, Table, UncategorizedText, ...


def extract(filename: str, data: bytes) -> list[Page]:
    try:
        from unstructured.partition.auto import partition
    except ImportError as e:
        raise RuntimeError('EXTRACTOR=unstructured needs the package: uv add "unstructured[all-docs]"') from e
    try:
        # metadata_filename: the type is guessed from the name and the bytes; nothing is written to disk
        elements = partition(file=BytesIO(data), metadata_filename=filename, strategy="auto",
                             infer_table_structure=True)
    except ValueError as e:  # unknown or broken file type
        raise UnreadableDocument(f"Unstructured couldn't read this file: {e}") from e

    pages: dict[int | None, list[str]] = {}
    for element in elements:
        line = _line(element)
        if line:
            pages.setdefault(element.metadata.page_number, []).append(line)
    return [(page, "\n\n".join(lines)) for page, lines in sorted(pages.items(), key=lambda kv: kv[0] or 0)]
