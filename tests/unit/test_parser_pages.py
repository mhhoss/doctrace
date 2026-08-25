"""`extract_pages` — the page-boundary primitive the extraction pipeline (ADR-24)
attaches citations against.

`extract_text` is defined in terms of `extract_pages` (parser.py), so every test here
also guards against `extract_text`'s existing behavior silently drifting.
"""

from __future__ import annotations

from app.documents.parser import extract_pages, extract_text
from tests.docx_fixtures import build_docx
from tests.pdf_fixtures import build_pdf

PAGE_ONE = "This is the first page of the report."
PAGE_TWO = "This is the second page, with different content."
PAGE_THREE = "این صفحه‌ی سوم است."  # Persian: "this is the third page"


class TestPdfPages:
    def test_single_page_pdf_returns_one_element(self) -> None:
        pdf = build_pdf([PAGE_ONE])
        pages = extract_pages(file_type="pdf", content=pdf)
        assert len(pages) == 1
        assert PAGE_ONE.split()[0] in pages[0]

    def test_multi_page_pdf_returns_one_element_per_page_in_order(self) -> None:
        pdf = build_pdf([PAGE_ONE, PAGE_TWO, PAGE_THREE])
        pages = extract_pages(file_type="pdf", content=pdf)
        assert len(pages) == 3
        # `pdftotext -layout` pads inter-word spacing to preserve column position, so
        # match on single words rather than an exact phrase (matching test_parser.py's
        # own `_squeeze` rationale).
        assert "first" in pages[0] and "page" in pages[0]
        assert "second" in pages[1] and "page" in pages[1]
        assert "صفحه" in pages[2]

    def test_each_page_is_stripped_and_non_empty(self) -> None:
        pdf = build_pdf([PAGE_ONE, PAGE_TWO])
        pages = extract_pages(file_type="pdf", content=pdf)
        for page in pages:
            assert page == page.strip()
            assert page != ""

    def test_extract_text_is_exactly_extract_pages_joined(self) -> None:
        """Pins the relationship, not just each function's own output: if this ever
        drifts, `extract_text`'s existing 394-test-covered behavior and the new
        extraction pipeline's page attribution would silently disagree with each other."""
        pdf = build_pdf([PAGE_ONE, PAGE_TWO, PAGE_THREE])
        pages = extract_pages(file_type="pdf", content=pdf)
        text = extract_text(file_type="pdf", content=pdf)
        assert text == "\n\n".join(pages)


class TestDocxAndTxtHaveNoRealPages:
    """DOCX/TXT have no page concept — both must report a single block, never a faked
    per-page split, so downstream extraction code has an unambiguous signal to treat
    page attribution as unavailable rather than guessing."""

    def test_docx_returns_a_single_block(self) -> None:
        docx_bytes = build_docx(["First paragraph.", "Second paragraph."])
        pages = extract_pages(file_type="docx", content=docx_bytes)
        assert len(pages) == 1
        assert "First paragraph" in pages[0]
        assert "Second paragraph" in pages[0]

    def test_docx_extract_pages_matches_extract_text(self) -> None:
        docx_bytes = build_docx(["Only paragraph."])
        assert extract_pages(file_type="docx", content=docx_bytes) == [
            extract_text(file_type="docx", content=docx_bytes)
        ]

    def test_txt_returns_a_single_block(self) -> None:
        content = b"Line one.\nLine two."
        pages = extract_pages(file_type="txt", content=content)
        assert len(pages) == 1

    def test_txt_extract_pages_matches_extract_text(self) -> None:
        content = b"Just some text."
        assert extract_pages(file_type="txt", content=content) == [
            extract_text(file_type="txt", content=content)
        ]
