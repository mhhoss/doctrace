"""Original uploaded file bytes, keyed by content-hash document_id (ADR-3's identity
space, reused here rather than inventing a second one) — backs `GET
/documents/{document_id}/file`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.storage.db import Database


@dataclass(frozen=True)
class StoredFile:
    document_id: str
    filename: str
    file_type: str
    content: bytes


class FileStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, *, document_id: str, filename: str, file_type: str, content: bytes) -> None:
        """Idempotent: identical content always has the same document_id (ADR-3), so
        re-saving is a no-op in effect — `INSERT OR REPLACE` keeps the latest filename
        without erroring on a duplicate upload."""
        self._db.execute(
            "INSERT OR REPLACE INTO original_files "
            "(document_id, filename, file_type, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (document_id, filename, file_type, content, time.time()),
        )

    def get(self, document_id: str) -> StoredFile | None:
        row = self._db.query_one(
            "SELECT document_id, filename, file_type, content FROM original_files "
            "WHERE document_id = ?",
            (document_id,),
        )
        if row is None:
            return None
        return StoredFile(
            document_id=row["document_id"],
            filename=row["filename"],
            file_type=row["file_type"],
            content=row["content"],
        )

    def delete(self, document_id: str) -> None:
        """Idempotent: deleting an absent document_id is a no-op, not an error."""
        self._db.execute(
            "DELETE FROM original_files WHERE document_id = ?", (document_id,)
        )
