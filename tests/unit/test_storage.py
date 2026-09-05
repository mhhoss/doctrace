"""Persistence layer (ADR-29): `FileStore` and `ExtractionStore` round-trip through a
real SQLite file, and both survive a fresh instance reopening the same `Database` path
— the actual restart-survival guarantee, not just an in-process round-trip.
"""

from __future__ import annotations

from pathlib import Path

from app.extraction.models import (
    ExtractedItem,
    ExtractionOutcome,
    ExtractionStatus,
    SectionOutcome,
    SectionStatus,
    VerificationStatus,
)
from app.extraction.store import ExtractionStore
from app.storage.db import Database
from app.storage.files import FileStore


def _outcome(document_id: str) -> ExtractionOutcome:
    return ExtractionOutcome(
        document_id=document_id,
        filename="report.pdf",
        status=ExtractionStatus.SUCCEEDED,
        items=[
            ExtractedItem(
                item_id="item-1",
                document_id=document_id,
                category="deadline",
                text="Delivery within 30 days",
                quote="30 days",
                section_label="Article 1",
                page_start=2,
                page_end=2,
                located=True,
                confidence=0.9,
                verification_status=VerificationStatus.VERIFIED,
                verification_note="supported by the quoted passage",
            )
        ],
        sections=[
            SectionOutcome(
                section_id="sec-1", label="Article 1", status=SectionStatus.SUCCEEDED, item_count=1
            )
        ],
    )


class TestFileStore:
    def test_a_saved_file_can_be_read_back(self, db: Database) -> None:
        store = FileStore(db)
        store.save(document_id="doc-1", filename="report.pdf", file_type="pdf", content=b"%PDF-1.4...")

        stored = store.get("doc-1")

        assert stored is not None
        assert stored.filename == "report.pdf"
        assert stored.file_type == "pdf"
        assert stored.content == b"%PDF-1.4..."

    def test_unknown_document_id_returns_none(self, db: Database) -> None:
        assert FileStore(db).get("does-not-exist") is None

    def test_resaving_the_same_document_id_replaces_the_filename(self, db: Database) -> None:
        """ADR-3 content-hash identity: re-uploading the same bytes under a new name
        is expected to happen (e.g. via `/documents` and `/extract` both), and should
        not error."""
        store = FileStore(db)
        store.save(document_id="doc-1", filename="first.pdf", file_type="pdf", content=b"data")
        store.save(document_id="doc-1", filename="second.pdf", file_type="pdf", content=b"data")

        reopened = FileStore(db).get("doc-1")
        assert reopened is not None
        assert reopened.filename == "second.pdf"

    def test_survives_a_fresh_store_over_the_same_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "restart.db"
        FileStore(Database(db_path)).save(
            document_id="doc-1", filename="report.pdf", file_type="pdf", content=b"data"
        )

        reopened = FileStore(Database(db_path)).get("doc-1")

        assert reopened is not None
        assert reopened.content == b"data"

    def test_delete_removes_the_file(self, db: Database) -> None:
        store = FileStore(db)
        store.save(document_id="doc-1", filename="report.pdf", file_type="pdf", content=b"data")

        store.delete("doc-1")

        assert store.get("doc-1") is None

    def test_deleting_an_unknown_document_id_is_not_an_error(self, db: Database) -> None:
        FileStore(db).delete("does-not-exist")


class TestExtractionStore:
    def test_a_saved_outcome_round_trips_exactly(self, db: Database) -> None:
        store = ExtractionStore(db)
        outcome = _outcome("doc-1")
        store.save(outcome)

        assert store.get("doc-1") == outcome

    def test_unknown_document_id_returns_none(self, db: Database) -> None:
        assert ExtractionStore(db).get("does-not-exist") is None

    def test_survives_a_fresh_store_over_the_same_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "restart.db"
        outcome = _outcome("doc-1")
        ExtractionStore(Database(db_path)).save(outcome)

        assert ExtractionStore(Database(db_path)).get("doc-1") == outcome

    def test_delete_removes_the_result(self, db: Database) -> None:
        store = ExtractionStore(db)
        store.save(_outcome("doc-1"))

        store.delete("doc-1")

        assert store.get("doc-1") is None

    def test_deleting_an_unknown_document_id_is_not_an_error(self, db: Database) -> None:
        ExtractionStore(db).delete("does-not-exist")
