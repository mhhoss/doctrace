"""Extraction data model: the LLM-facing schemas and the domain types built from them.

`ExtractedItemLLM`/`SectionExtractionResult`/`VerificationResult` are the wire contract
with the LLM (pydantic, validated by `structured_predict`). `Section`/`ExtractedItem`/
`SectionOutcome`/`ExtractionOutcome` are this project's own domain types, mirroring
`documents/processor.py`'s `Chunk` and `rag/indexer.py`'s `IngestOutcome` conventions —
frozen dataclasses, no raised failures, one `Outcome` per unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

ItemCategory = Literal[
    "obligation", "requirement", "deadline", "prohibition", "definition", "other"
]


class ExtractedItemLLM(BaseModel):
    """One requirement/obligation, as the LLM reports it for one section."""

    category: ItemCategory
    text: str = Field(description="A self-contained statement of the requirement.")
    quote: str = Field(
        description="A verbatim span from the supplied section text that supports "
        "this item. Must be copied exactly, not paraphrased — it is used to locate "
        "the item's source page."
    )


class SectionExtractionResult(BaseModel):
    items: list[ExtractedItemLLM] = Field(default_factory=list)


class VerificationResult(BaseModel):
    supported: bool = Field(
        description="Whether the passage actually supports the claim as stated, "
        "without extrapolation."
    )
    reason: str = Field(description="One sentence explaining the verdict.")


@dataclass(frozen=True)
class Section:
    """One unit of text sent to the LLM in a single extraction call."""

    section_id: str
    label: str | None
    text: str


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    FAILED = "failed"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class ExtractedItem:
    """One requirement/obligation, resolved to this project's own identity and page
    attribution — the unit the API and UI both consume."""

    item_id: str
    document_id: str
    category: ItemCategory
    text: str
    quote: str
    section_label: str | None
    page_start: int | None
    page_end: int | None
    located: bool
    """Whether `quote` was found in the source text (exactly or fuzzily). Always
    `False` for DOCX/TXT — page attribution is never attempted for them, since
    neither has a real page concept; that is expected, not a failure signal."""
    confidence: float
    verification_status: VerificationStatus
    verification_note: str | None = None


class SectionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class SectionOutcome:
    section_id: str
    label: str | None
    status: SectionStatus
    item_count: int
    error: str | None = None


class ExtractionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    """At least one section produced items, even if some sections failed."""
    FAILED = "failed"
    """No extractable text, or every section failed."""


@dataclass(frozen=True)
class ExtractionOutcome:
    document_id: str
    filename: str
    status: ExtractionStatus
    items: list[ExtractedItem] = field(default_factory=list)
    sections: list[SectionOutcome] = field(default_factory=list)
    error: str | None = None

    @property
    def sections_total(self) -> int:
        return len(self.sections)

    @property
    def sections_succeeded(self) -> int:
        return sum(1 for s in self.sections if s.status == SectionStatus.SUCCEEDED)

    @property
    def sections_failed(self) -> int:
        return sum(1 for s in self.sections if s.status == SectionStatus.FAILED)
