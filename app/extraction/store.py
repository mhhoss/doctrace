"""Extraction results, persisted (ADR-29) — survives a restart, unlike the in-memory
dict this used before. Serialized to JSON since `ExtractionOutcome` is a plain
dataclass tree with no external references (no Chroma/DB objects inside it).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict

from app.extraction.models import (
    ExtractedItem,
    ExtractionOutcome,
    ExtractionStatus,
    SectionOutcome,
    SectionStatus,
    VerificationStatus,
)
from app.storage.db import Database


class ExtractionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, outcome: ExtractionOutcome) -> None:
        payload = json.dumps(_to_dict(outcome), ensure_ascii=False)
        self._db.execute(
            "INSERT OR REPLACE INTO extraction_results (document_id, data, updated_at) "
            "VALUES (?, ?, ?)",
            (outcome.document_id, payload, time.time()),
        )

    def get(self, document_id: str) -> ExtractionOutcome | None:
        row = self._db.query_one(
            "SELECT data FROM extraction_results WHERE document_id = ?",
            (document_id,),
        )
        if row is None:
            return None
        return _from_dict(json.loads(row["data"]))

    def delete(self, document_id: str) -> None:
        """Idempotent, matching `FileStore.delete`/`VectorStore.delete_document`."""
        self._db.execute(
            "DELETE FROM extraction_results WHERE document_id = ?", (document_id,)
        )


def _to_dict(outcome: ExtractionOutcome) -> dict:
    data = asdict(outcome)
    data["status"] = outcome.status.value
    for item, section in zip(data["items"], outcome.items, strict=True):
        item["verification_status"] = section.verification_status.value
    for section, domain_section in zip(data["sections"], outcome.sections, strict=True):
        section["status"] = domain_section.status.value
    return data


def _from_dict(data: dict) -> ExtractionOutcome:
    return ExtractionOutcome(
        document_id=data["document_id"],
        filename=data["filename"],
        status=ExtractionStatus(data["status"]),
        items=[
            ExtractedItem(
                item_id=item["item_id"],
                document_id=item["document_id"],
                category=item["category"],
                text=item["text"],
                quote=item["quote"],
                section_label=item["section_label"],
                page_start=item["page_start"],
                page_end=item["page_end"],
                located=item["located"],
                confidence=item["confidence"],
                verification_status=VerificationStatus(item["verification_status"]),
                verification_note=item["verification_note"],
            )
            for item in data["items"]
        ],
        sections=[
            SectionOutcome(
                section_id=section["section_id"],
                label=section["label"],
                status=SectionStatus(section["status"]),
                item_count=section["item_count"],
                error=section["error"],
            )
            for section in data["sections"]
        ],
        error=data["error"],
    )
