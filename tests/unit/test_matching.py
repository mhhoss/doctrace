from __future__ import annotations

from app.extraction.matching import deduplicate, locate_quote
from app.extraction.models import ExtractedItem, ItemCategory, VerificationStatus

PAGES = [
    "The contractor must deliver within thirty days of signing.",
    "The client must provide a bank guarantee before work begins.",
    "Late delivery triggers a penalty of one percent per day.",
]


def _item(item_id: str, category: ItemCategory, text: str) -> ExtractedItem:
    return ExtractedItem(
        item_id=item_id,
        document_id="d",
        category=category,
        text=text,
        quote="q",
        section_label=None,
        page_start=1,
        page_end=1,
        located=True,
        confidence=1.0,
        verification_status=VerificationStatus.NOT_RUN,
    )


class TestLocateQuote:
    def test_exact_match_on_the_correct_page(self) -> None:
        assert locate_quote(PAGES, "deliver within thirty days") == (1, 1)
        assert locate_quote(PAGES, "bank guarantee") == (2, 2)
        assert locate_quote(PAGES, "penalty of one percent") == (3, 3)

    def test_no_match_anywhere_returns_none(self) -> None:
        assert locate_quote(PAGES, "something about spaceships and dinosaurs") is None

    def test_empty_quote_returns_none(self) -> None:
        assert locate_quote(PAGES, "") is None
        assert locate_quote(PAGES, "   ") is None

    def test_quote_straddling_a_page_break_resolves_to_both_pages(self) -> None:
        pages = ["...the deadline is", "thirty days after signing."]
        assert locate_quote(pages, "the deadline is thirty days after signing") == (1, 2)

    def test_whitespace_differences_do_not_prevent_a_match(self) -> None:
        pages = ["The   contractor    must   deliver."]
        assert locate_quote(pages, "contractor must deliver") == (1, 1)

    def test_close_paraphrase_locates_fuzzily(self) -> None:
        result = locate_quote(PAGES, "the contractor must deliver within thirty days")
        assert result == (1, 1)

    def test_single_page_document_still_works(self) -> None:
        assert locate_quote(["Only one page of text here."], "one page") == (1, 1)


class TestDeduplicate:
    def test_near_duplicate_text_in_the_same_category_collapses_to_one(self) -> None:
        items = [
            _item("1", "deadline", "Delivery must occur within 30 days"),
            _item("2", "deadline", "Delivery must occur within 30 days."),
        ]
        result = deduplicate(items)
        assert len(result) == 1
        assert result[0].item_id == "1"  # first occurrence wins

    def test_distinct_items_in_the_same_category_both_survive(self) -> None:
        items = [
            _item("1", "deadline", "Delivery must occur within 30 days"),
            _item("2", "deadline", "Payment is due within 60 days"),
        ]
        result = deduplicate(items)
        assert len(result) == 2

    def test_near_duplicate_text_in_different_categories_both_survive(self) -> None:
        """Dedup is category-scoped: identical text under a different category is a
        different claim (e.g. a requirement vs. a deadline framing of the same
        sentence), not noise to collapse."""
        items = [
            _item("1", "deadline", "Delivery within 30 days"),
            _item("2", "requirement", "Delivery within 30 days"),
        ]
        result = deduplicate(items)
        assert len(result) == 2

    def test_empty_list_returns_empty_list(self) -> None:
        assert deduplicate([]) == []
