"""Document → sections → structured extraction → page attachment → dedup → verify →
`ExtractionOutcome`. Mirrors `rag/engine.py::ingest_file`'s shape deliberately: never
raises, isolates one section's failure from the rest (ADR-7, one layer down), and
never touches the vector store — extraction and retrieval share only `documents/`
(ADR-24's design note).
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from app.documents.parser import extract_pages
from app.documents.processor import normalize_text
from app.extraction import llm_calls
from app.extraction.matching import deduplicate, locate_quote
from app.extraction.models import (
    ExtractedItem,
    ExtractionOutcome,
    ExtractionStatus,
    SectionOutcome,
    SectionStatus,
    VerificationStatus,
)
from app.extraction.sectionize import sectionize
from app.observability import log_event

if TYPE_CHECKING:
    from llama_index.core.llms import LLM

logger = logging.getLogger(__name__)

# Confidence starts at 1.0 and is discounted for each signal that makes an item less
# trustworthy than "exact quote match, verified true." Never the LLM's own claimed
# confidence — see ADR-25 (this pipeline's design note) for why.
_CONFIDENCE_UNLOCATED_PENALTY = 0.4
_CONFIDENCE_FUZZY_LOCATE_PENALTY = 0.15
_CONFIDENCE_VERIFICATION_FAILED_PENALTY = 0.5
_CONFIDENCE_NOT_VERIFIED_PENALTY = 0.1


def extract_document(
    *,
    document_id: str,
    filename: str,
    file_type: str,
    content: bytes,
    llm: LLM,
    verify: bool = True,
) -> ExtractionOutcome:
    """Run one document through the full extraction pipeline.

    Every failure mode — no extractable text, every section failing — becomes a
    `FAILED` `ExtractionOutcome`, never a raised exception (R-09's convention,
    applied here). A partial failure (some sections succeed, some fail) is reported
    as `SUCCEEDED` with the failed sections visible in `.sections`, exactly as
    `IngestionJob` already reports per-file outcomes within one job.
    """
    try:
        raw_pages = extract_pages(file_type=file_type, content=content)
    except Exception as error:  # noqa: BLE001 - any parse failure is a domain outcome
        detail = str(error) or type(error).__name__
        outcome = ExtractionOutcome(
            document_id=document_id,
            filename=filename,
            status=ExtractionStatus.FAILED,
            error=detail,
        )
        _log_outcome(outcome)
        return outcome

    pages = [normalize_text(page) for page in raw_pages]
    has_real_pages = file_type == "pdf"
    full_text = "\n\n".join(pages)

    sections = sectionize(full_text, document_id=document_id)
    if not sections:
        outcome = ExtractionOutcome(
            document_id=document_id,
            filename=filename,
            status=ExtractionStatus.FAILED,
            error="No extractable text was found in this file.",
        )
        _log_outcome(outcome)
        return outcome

    section_outcomes: list[SectionOutcome] = []
    raw_items: list[ExtractedItem] = []

    for section in sections:
        try:
            result = llm_calls.extract_section_items(
                llm, section_text=section.text, section_label=section.label
            )
        except Exception as error:  # noqa: BLE001 - isolate one section's failure
            section_outcomes.append(
                SectionOutcome(
                    section_id=section.section_id,
                    label=section.label,
                    status=SectionStatus.FAILED,
                    item_count=0,
                    error=str(error) or type(error).__name__,
                )
            )
            continue

        section_items = [
            _resolve_item(
                item,
                document_id=document_id,
                section_label=section.label,
                pages=pages,
                has_real_pages=has_real_pages,
            )
            for item in result.items
        ]
        raw_items.extend(section_items)
        section_outcomes.append(
            SectionOutcome(
                section_id=section.section_id,
                label=section.label,
                status=SectionStatus.SUCCEEDED,
                item_count=len(section_items),
            )
        )

    items = deduplicate(raw_items)
    if verify:
        items = [_verify(item, llm=llm, pages=pages) for item in items]

    status = (
        ExtractionStatus.SUCCEEDED
        if any(s.status == SectionStatus.SUCCEEDED for s in section_outcomes)
        else ExtractionStatus.FAILED
    )
    outcome = ExtractionOutcome(
        document_id=document_id,
        filename=filename,
        status=status,
        items=items,
        sections=section_outcomes,
        error=None if status == ExtractionStatus.SUCCEEDED else "Every section failed.",
    )
    _log_outcome(outcome)
    return outcome


def _resolve_item(
    item: object,
    *,
    document_id: str,
    section_label: str | None,
    pages: list[str],
    has_real_pages: bool,
) -> ExtractedItem:
    from app.extraction.models import ExtractedItemLLM

    assert isinstance(item, ExtractedItemLLM)  # narrows for type checkers only

    located_pages = locate_quote(pages, item.quote) if has_real_pages else None
    page_start, page_end = located_pages if located_pages else (None, None)
    located = located_pages is not None

    confidence = 1.0
    if has_real_pages and not located:
        confidence -= _CONFIDENCE_UNLOCATED_PENALTY
    elif located_pages is not None and page_start != page_end:
        confidence -= _CONFIDENCE_FUZZY_LOCATE_PENALTY

    return ExtractedItem(
        item_id=_item_id(document_id, item.category, item.text),
        document_id=document_id,
        category=item.category,
        text=item.text,
        quote=item.quote,
        section_label=section_label,
        page_start=page_start,
        page_end=page_end,
        located=located,
        confidence=max(confidence, 0.0),
        verification_status=VerificationStatus.NOT_RUN,
    )


def _verify(item: ExtractedItem, *, llm: LLM, pages: list[str]) -> ExtractedItem:
    passage = _passage_for(item, pages)
    try:
        result = llm_calls.verify_item(llm, claim=item.text, passage=passage)
    except Exception as error:  # noqa: BLE001 - a verifier failure is a status, not a crash
        return _with_status(
            item,
            VerificationStatus.NOT_RUN,
            str(error) or type(error).__name__,
            confidence_delta=0.0,
        )
    if result.supported:
        return _with_status(item, VerificationStatus.VERIFIED, result.reason, 0.0)
    return _with_status(
        item,
        VerificationStatus.FAILED,
        result.reason,
        confidence_delta=-_CONFIDENCE_VERIFICATION_FAILED_PENALTY,
    )


def _with_status(
    item: ExtractedItem,
    status: VerificationStatus,
    note: str,
    confidence_delta: float,
) -> ExtractedItem:
    from dataclasses import replace

    return replace(
        item,
        verification_status=status,
        verification_note=note,
        confidence=max(item.confidence + confidence_delta, 0.0),
    )


def _passage_for(item: ExtractedItem, pages: list[str]) -> str:
    if item.page_start is None:
        return item.quote
    start, end = item.page_start, item.page_end or item.page_start
    return "\n\n".join(pages[start - 1 : end])


def _item_id(document_id: str, category: str, text: str) -> str:
    digest = hashlib.sha256(f"{document_id}|{category}|{text}".encode()).hexdigest()
    return digest[:16]


def _log_outcome(outcome: ExtractionOutcome) -> None:
    level = logging.WARNING if outcome.status == ExtractionStatus.FAILED else logging.INFO
    log_event(
        logger,
        level,
        "document extracted",
        document_id=outcome.document_id,
        filename=outcome.filename,
        status=outcome.status.value,
        item_count=len(outcome.items),
        sections_total=outcome.sections_total,
        sections_failed=outcome.sections_failed,
        error=outcome.error,
    )
