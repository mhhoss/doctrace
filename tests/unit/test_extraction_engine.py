"""`extraction.engine.extract_document` — section isolation, page attachment, dedup,
and verification, all exercised without a real LLM call (`llm_calls` functions are
monkeypatched at their module-level call site, matching this project's existing
`build_embedding_model`/`build_llm` monkeypatch convention — no fake LLM needs to
correctly emulate structured/tool-calling output).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.extraction import engine, llm_calls
from app.extraction.models import (
    ExtractedItemLLM,
    ExtractionStatus,
    SectionExtractionResult,
    VerificationResult,
    VerificationStatus,
)
from tests.pdf_fixtures import build_pdf

_UNUSED_LLM: Any = object()  # llm_calls is monkeypatched, so the real object never matters

CLAUSE_PDF_TEXT = [
    "ماده ۱: پیمانکار موظف است ظرف ۳۰ روز کار را تحویل دهد.",
    "ماده ۲: کارفرما موظف است ضمانت‌نامه ارائه دهد.",
]


def _pdf() -> bytes:
    return build_pdf(CLAUSE_PDF_TEXT)


class TestNoExtractableText:
    def test_empty_txt_is_a_failed_outcome_not_an_exception(self) -> None:
        outcome = engine.extract_document(
            document_id="d1", filename="empty.txt", file_type="txt",
            content=b"   ", llm=_UNUSED_LLM,
        )
        assert outcome.status == ExtractionStatus.FAILED
        assert "No extractable text" in (outcome.error or "")
        assert outcome.items == []


class TestSectionIsolation:
    def test_one_failed_section_does_not_abort_the_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {"n": 0}

        def fake_extract(llm: object, *, section_text: str, section_label: str | None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("provider timeout")
            return SectionExtractionResult(
                items=[
                    ExtractedItemLLM(
                        category="obligation",
                        text="Guarantee must be provided",
                        quote="ضمانت",
                    )
                ]
            )

        monkeypatch.setattr(llm_calls, "extract_section_items", fake_extract)

        outcome = engine.extract_document(
            document_id="d1", filename="doc.pdf", file_type="pdf",
            content=_pdf(), llm=_UNUSED_LLM, verify=False,
        )

        assert outcome.status == ExtractionStatus.SUCCEEDED
        assert outcome.sections_total == 2
        assert outcome.sections_failed == 1
        assert outcome.sections_succeeded == 1
        assert len(outcome.items) == 1

    def test_every_section_failing_is_a_failed_document_outcome(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def always_fails(llm: object, *, section_text: str, section_label: str | None):
            raise RuntimeError("schema validation failed")

        monkeypatch.setattr(llm_calls, "extract_section_items", always_fails)

        outcome = engine.extract_document(
            document_id="d1", filename="doc.pdf", file_type="pdf",
            content=_pdf(), llm=_UNUSED_LLM, verify=False,
        )

        assert outcome.status == ExtractionStatus.FAILED
        assert outcome.items == []
        assert outcome.sections_failed == outcome.sections_total


class TestPageAttachment:
    def test_located_quote_gets_the_correct_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_extract(llm: object, *, section_text: str, section_label: str | None):
            if "ظرف" in section_text:
                return SectionExtractionResult(
                    items=[
                        ExtractedItemLLM(
                            category="deadline",
                            text="Must deliver within 30 days",
                            quote="ظرف",
                        )
                    ]
                )
            return SectionExtractionResult(items=[])

        monkeypatch.setattr(llm_calls, "extract_section_items", fake_extract)

        outcome = engine.extract_document(
            document_id="d1", filename="doc.pdf", file_type="pdf",
            content=_pdf(), llm=_UNUSED_LLM, verify=False,
        )

        assert len(outcome.items) == 1
        item = outcome.items[0]
        assert item.located is True
        assert item.page_start == 1
        assert item.page_end == 1
        assert item.confidence == 1.0

    def test_unlocatable_quote_is_kept_flagged_not_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_extract(llm: object, *, section_text: str, section_label: str | None):
            return SectionExtractionResult(
                items=[
                    ExtractedItemLLM(
                        category="other",
                        text="A claim with a fabricated quote",
                        quote="this text does not appear anywhere in the document",
                    )
                ]
            )

        monkeypatch.setattr(llm_calls, "extract_section_items", fake_extract)

        outcome = engine.extract_document(
            document_id="d1", filename="doc.pdf", file_type="pdf",
            content=_pdf(), llm=_UNUSED_LLM, verify=False,
        )

        # Both sections produce the same unlocatable item, then dedup collapses them.
        assert len(outcome.items) == 1
        item = outcome.items[0]
        assert item.located is False
        assert item.page_start is None
        assert item.confidence < 1.0

    def test_docx_never_gets_a_page_number(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.docx_fixtures import build_docx

        def fake_extract(llm: object, *, section_text: str, section_label: str | None):
            return SectionExtractionResult(
                items=[
                    ExtractedItemLLM(
                        category="obligation", text="Something required", quote="required"
                    )
                ]
            )

        monkeypatch.setattr(llm_calls, "extract_section_items", fake_extract)

        outcome = engine.extract_document(
            document_id="d1", filename="doc.docx", file_type="docx",
            content=build_docx(["Something is required by this contract."]),
            llm=_UNUSED_LLM, verify=False,
        )

        assert len(outcome.items) == 1
        assert outcome.items[0].page_start is None
        assert outcome.items[0].located is False


class TestVerification:
    def test_verified_item_keeps_full_confidence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            llm_calls,
            "extract_section_items",
            lambda llm, **kw: SectionExtractionResult(
                items=[
                    ExtractedItemLLM(category="obligation", text="X must happen", quote="ماده")
                ]
            )
            if "ظرف" in kw.get("section_text", "")
            else SectionExtractionResult(items=[]),
        )
        monkeypatch.setattr(
            llm_calls, "verify_item",
            lambda llm, **kw: VerificationResult(supported=True, reason="matches"),
        )

        outcome = engine.extract_document(
            document_id="d1", filename="doc.pdf", file_type="pdf",
            content=_pdf(), llm=_UNUSED_LLM, verify=True,
        )

        assert outcome.items[0].verification_status == VerificationStatus.VERIFIED
        assert outcome.items[0].confidence == 1.0

    def test_failed_verification_flags_but_does_not_drop_the_item(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            llm_calls,
            "extract_section_items",
            lambda llm, **kw: SectionExtractionResult(
                items=[
                    ExtractedItemLLM(category="obligation", text="X must happen", quote="ماده")
                ]
            )
            if "ظرف" in kw.get("section_text", "")
            else SectionExtractionResult(items=[]),
        )
        monkeypatch.setattr(
            llm_calls, "verify_item",
            lambda llm, **kw: VerificationResult(
                supported=False, reason="claim overstates the passage"
            ),
        )

        outcome = engine.extract_document(
            document_id="d1", filename="doc.pdf", file_type="pdf",
            content=_pdf(), llm=_UNUSED_LLM, verify=True,
        )

        assert len(outcome.items) == 1
        item = outcome.items[0]
        assert item.verification_status == VerificationStatus.FAILED
        assert item.verification_note == "claim overstates the passage"
        assert item.confidence < 1.0

    def test_verifier_error_is_not_run_not_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            llm_calls,
            "extract_section_items",
            lambda llm, **kw: SectionExtractionResult(
                items=[
                    ExtractedItemLLM(category="obligation", text="X must happen", quote="ماده")
                ]
            )
            if "ظرف" in kw.get("section_text", "")
            else SectionExtractionResult(items=[]),
        )

        def verify_raises(llm: object, **kw: object) -> VerificationResult:
            raise RuntimeError("network error")

        monkeypatch.setattr(llm_calls, "verify_item", verify_raises)

        outcome = engine.extract_document(
            document_id="d1", filename="doc.pdf", file_type="pdf",
            content=_pdf(), llm=_UNUSED_LLM, verify=True,
        )

        assert outcome.items[0].verification_status == VerificationStatus.NOT_RUN
        assert outcome.items[0].confidence == pytest.approx(outcome.items[0].confidence)


class TestDeduplication:
    def test_near_duplicate_items_from_different_sections_are_collapsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_extract(llm: object, *, section_text: str, section_label: str | None):
            return SectionExtractionResult(
                items=[
                    ExtractedItemLLM(
                        category="deadline",
                        text="Delivery must occur within 30 days of signing",
                        quote="ظرف" if "ظرف" in section_text else "کارفرما",
                    )
                ]
            )

        monkeypatch.setattr(llm_calls, "extract_section_items", fake_extract)

        outcome = engine.extract_document(
            document_id="d1", filename="doc.pdf", file_type="pdf",
            content=_pdf(), llm=_UNUSED_LLM, verify=False,
        )

        assert len(outcome.items) == 1
