"""In-memory extraction results, keyed by document_id — the same simplicity/lifecycle
choice `rag/jobs.py`'s `JobStore` already makes (one per process, lost on restart);
replacing this with durable (SQLite) storage is a separate, later change, not a
reason to block the extraction pipeline reaching the API now.
"""

from __future__ import annotations

import threading

from app.extraction.models import ExtractionOutcome


class ExtractionStore:
    def __init__(self) -> None:
        self._results: dict[str, ExtractionOutcome] = {}
        self._lock = threading.Lock()

    def save(self, outcome: ExtractionOutcome) -> None:
        with self._lock:
            self._results[outcome.document_id] = outcome

    def get(self, document_id: str) -> ExtractionOutcome | None:
        with self._lock:
            return self._results.get(document_id)
