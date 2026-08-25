from __future__ import annotations

from app.extraction.sectionize import sectionize


class TestEmptyText:
    def test_empty_text_returns_no_sections(self) -> None:
        assert sectionize("", document_id="d") == []

    def test_whitespace_only_returns_no_sections(self) -> None:
        assert sectionize("   \n\n  ", document_id="d") == []


class TestClauseHeaderDetection:
    def test_persian_numbered_clauses_split_into_one_section_each(self) -> None:
        text = "ماده ۱ متن اول است.\n\nماده ۲ متن دوم است.\n\nماده ۳ متن سوم است."
        sections = sectionize(text, document_id="d")
        assert len(sections) == 3
        assert all(s.label is not None for s in sections)

    def test_a_colon_reordered_next_to_the_digit_still_matches(self) -> None:
        """Real RTL PDF extraction can reorder a colon next to a digit run — see
        eval/README.md's documented bidi artifact — so this must not silently fall
        back to windowing on real Persian documents with clause numbering."""
        text = "ماده :۱ متن اول است.\n\nماده :۲ متن دوم است."
        sections = sectionize(text, document_id="d")
        assert len(sections) == 2

    def test_english_numbered_sections_are_detected(self) -> None:
        text = "Section 1 First requirement.\n\nSection 2 Second requirement."
        sections = sectionize(text, document_id="d")
        assert len(sections) == 2

    def test_section_ids_are_unique_and_ordered(self) -> None:
        text = "\n\n".join(f"Clause {i} text here." for i in range(1, 6))
        sections = sectionize(text, document_id="d")
        ids = [s.section_id for s in sections]
        assert len(ids) == len(set(ids))
        assert ids == sorted(ids)


class TestWindowedFallback:
    def test_sparse_or_absent_headers_falls_back_to_windowing(self) -> None:
        text = "This is plain flowing prose with no clause structure at all. " * 100
        sections = sectionize(text, document_id="d")
        assert len(sections) >= 1
        assert all(s.label is None for s in sections)

    def test_windowed_section_ids_are_unique(self) -> None:
        text = "Plain prose, no headings. " * 500
        sections = sectionize(text, document_id="d")
        ids = [s.section_id for s in sections]
        assert len(ids) == len(set(ids))

    def test_an_oversized_header_block_is_itself_windowed_without_id_collisions(
        self,
    ) -> None:
        """A single clause whose body exceeds the window size must still be covered,
        and its sub-sections must not collide with any other section's id."""
        huge_clause = "ماده ۱ " + ("متن طولانی. " * 1000)
        text = huge_clause + "\n\nماده ۲ متن کوتاه دوم."
        sections = sectionize(text, document_id="d")
        ids = [s.section_id for s in sections]
        assert len(ids) == len(set(ids))
        assert len(sections) > 2  # the oversized clause split into multiple windows
