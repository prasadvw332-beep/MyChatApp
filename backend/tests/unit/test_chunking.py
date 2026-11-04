"""Word (.docx) extraction: headings become sections, tables keep their rows."""

import io

import docx
import pytest

from app.rag.chunking import UnreadableDocumentError, chunk


def _docx(build) -> bytes:
    document = docx.Document()
    build(document)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def test_docx_headings_become_sections():
    def build(d):
        d.add_heading("Leave Policy", level=1)
        d.add_paragraph("Employees get 24 days of paid leave per year.")
        d.add_heading("Carry over", level=2)
        d.add_paragraph("Up to 5 unused days move to the next year.")

    docs = chunk("hr_policy.docx", _docx(build))

    assert [d.metadata["section"] for d in docs] == ["Leave Policy", "Leave Policy > Carry over"]
    assert docs[1].page_content.startswith("Document: hr policy\nSection: Leave Policy > Carry over")
    assert "5 unused days" in docs[1].page_content
    assert "page" not in docs[0].metadata  # Word files have no fixed pages


def test_docx_tables_keep_rows_in_order():
    def build(d):
        d.add_paragraph("Office locations:")
        table = d.add_table(rows=2, cols=2)
        for r, row in enumerate([("City", "Floor"), ("Pune", "4")]):
            for c, text in enumerate(row):
                table.cell(r, c).text = text
        d.add_paragraph("Visitors sign in at reception.")

    text = chunk("offices.docx", _docx(build))[0].page_content

    assert "City | Floor\nPune | 4" in text
    assert text.index("Office locations") < text.index("Pune") < text.index("Visitors")


@pytest.mark.parametrize("data", [b"not a zip file", _docx(lambda d: None)])
def test_broken_or_empty_docx_is_unreadable(data):
    with pytest.raises(UnreadableDocumentError):
        chunk("bad.docx", data)
