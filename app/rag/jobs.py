"""Background ingestion job tracking (ADR-17, durable since ADR-29).

`POST /documents` no longer embeds inline: it hands the uploaded files to a background
job and returns immediately, so the API stays responsive while the (CPU-bound, slow)
embedding backend works through them. This module owns exactly that concern — job
identity, per-file progress, and the background runner — and delegates every actual
unit of ingestion work to `rag/engine.ingest_file` (invariant 3 stays intact: this
module sequences *jobs*, not retrieval/generation, and never reimplements per-file
ingestion, dedup, or compensation logic).

Persisted to SQLite (`storage/db.py`) so a job's history survives a restart — it used
to be in-memory only. A restart never leaves partial data in the vector store (ADR-7:
nothing is written until a file's chunks are fully embedded), but a job left mid-run
would otherwise claim "running" forever; `JobStore.sweep_interrupted` (called once at
startup, `app/main.py`) closes that out — see its docstring.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.rag import engine
from app.rag.indexer import IngestOutcome
from app.storage.db import Database

if TYPE_CHECKING:
    from llama_index.core.base.embeddings.base import BaseEmbedding

    from app.storage.vector_store import VectorStore

# Held for a job's entire file loop (`run_ingestion_job`) so at most one job ever
# embeds at a time. The embedding backend already serializes requests internally
# (a single local model instance), so this doesn't change what the backend can do — it
# keeps this process's own behavior deterministic and sequential rather than opening
# concurrent HTTP calls into a backend never asked to handle them.
_ingestion_lock = threading.Lock()


class FileStatus(StrEnum):
    """Per-file progress within a job.

    `INDEXED`/`ALREADY_INDEXED`/`FAILED` mirror `indexer.IngestStatus` exactly — those
    three come straight from a real `IngestOutcome`. `QUEUED`/`PROCESSING` are job-only
    states with no domain equivalent. `SKIPPED` is also job-only: a file whose turn
    never came because cancellation was requested first (see `JobStore.request_cancel`)
    — never a real ingestion attempt, so it carries no `IngestOutcome` either.
    """

    QUEUED = "queued"
    PROCESSING = "processing"
    INDEXED = "indexed"
    ALREADY_INDEXED = "already_indexed"
    FAILED = "failed"
    SKIPPED = "skipped"


_TERMINAL_FILE_STATUSES = frozenset(
    {
        FileStatus.INDEXED,
        FileStatus.ALREADY_INDEXED,
        FileStatus.FAILED,
        FileStatus.SKIPPED,
    }
)


class JobStatus(StrEnum):
    """Derived from `IngestionJob.started_at`/`finished_at` — never stored directly."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"


@dataclass
class FileProgress:
    """One file's progress within a job."""

    filename: str
    status: FileStatus = FileStatus.QUEUED
    document_id: str | None = None
    chunk_count: int = 0
    error: str | None = None

    @classmethod
    def from_outcome(cls, outcome: IngestOutcome) -> FileProgress:
        return cls(
            filename=outcome.filename,
            status=FileStatus(outcome.status.value),
            document_id=outcome.document_id,
            chunk_count=outcome.chunk_count,
            error=outcome.error,
        )


@dataclass
class IngestionJob:
    """One ingestion request's lifecycle: queued -> running -> completed.

    Timestamps are wall-clock (`time.time()`), not `time.monotonic()` — this job is
    now persisted (ADR-29) and read back across process restarts, where a monotonic
    clock's reference point is meaningless.
    """

    job_id: str
    files: list[FileProgress]
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def total(self) -> int:
        return len(self.files)

    @property
    def completed_count(self) -> int:
        return sum(1 for f in self.files if f.status in _TERMINAL_FILE_STATUSES)

    @property
    def current_filename(self) -> str | None:
        for f in self.files:
            if f.status is FileStatus.PROCESSING:
                return f.filename
        return None

    @property
    def status(self) -> JobStatus:
        if self.finished_at is not None:
            return JobStatus.COMPLETED
        if self.started_at is not None:
            return JobStatus.RUNNING
        return JobStatus.QUEUED

    @property
    def eta_seconds(self) -> float | None:
        """Estimated remaining time, extrapolated only from this job's own completed
        files so far. `None` before the first file finishes or once the job is done —
        never a guess made before there is real progress to base it on."""
        completed = self.completed_count
        remaining = self.total - completed
        if completed == 0 or remaining <= 0 or self.started_at is None:
            return None
        elapsed = (self.finished_at or time.time()) - self.started_at
        return round((elapsed / completed) * remaining, 1)


class JobStore:
    """Persisted (SQLite) ingestion job registry. See module docstring."""

    def __init__(self, db: Database) -> None:
        self._db = db
        # Cancellation is a live, in-process signal only — not persisted.
        self._cancel_events: dict[str, threading.Event] = {}
        self._events_lock = threading.Lock()

    def create(self, filenames: list[str]) -> IngestionJob:
        job = IngestionJob(
            job_id=uuid.uuid4().hex,
            files=[FileProgress(filename=name) for name in filenames],
            created_at=time.time(),
        )
        self._save(job)
        with self._events_lock:
            self._cancel_events[job.job_id] = threading.Event()
        return job

    def get(self, job_id: str) -> IngestionJob | None:
        row = self._db.query_one(
            "SELECT data FROM ingestion_jobs WHERE job_id = ?", (job_id,)
        )
        return _job_from_row(row)

    def request_cancel(self, job_id: str) -> bool:
        """Ask a running job to stop starting new files (files already in progress
        still finish normally — see `run_ingestion_job`). Returns `False` if the job
        id is unknown, already completed, or not running in this process (a job
        resumed from a prior process has no live cancel event)."""
        job = self.get(job_id)
        if job is None or job.finished_at is not None:
            return False
        with self._events_lock:
            event = self._cancel_events.get(job_id)
        if event is None:
            return False
        event.set()
        return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._events_lock:
            event = self._cancel_events.get(job_id)
            return event.is_set() if event is not None else False

    def mark_started(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is not None and job.started_at is None:
            job.started_at = time.time()
            self._save(job)

    def mark_processing(self, job_id: str, index: int) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.files[index].status = FileStatus.PROCESSING
        self._save(job)

    def record_outcome(self, job_id: str, index: int, outcome: IngestOutcome) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.files[index] = FileProgress.from_outcome(outcome)
        self._save(job)

    def mark_skipped(self, job_id: str, index: int) -> None:
        """A file whose turn never came because cancellation was requested first."""
        job = self.get(job_id)
        if job is None:
            return
        job.files[index].status = FileStatus.SKIPPED
        job.files[index].error = None
        self._save(job)

    def mark_finished(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        job.finished_at = time.time()
        self._save(job)

    def sweep_interrupted(self) -> list[str]:
        """Called once at startup: a job with `started_at` set but `finished_at` unset
        can only mean the prior process died mid-run, so mark its unfinished files
        `failed` and close it out. No auto-resume — the original upload bytes are gone,
        and re-upload is already a cheap retry (ADR-3 dedup skips what finished).
        Returns the ids of jobs it closed out, for the startup log.
        """
        rows = self._db.query_all("SELECT data FROM ingestion_jobs")
        swept = []
        for row in rows:
            job = _job_from_row(row)
            if job is None or job.started_at is None or job.finished_at is not None:
                continue
            for file in job.files:
                if file.status in (FileStatus.QUEUED, FileStatus.PROCESSING):
                    file.status = FileStatus.FAILED
                    file.error = "Interrupted by a server restart. Re-upload to retry."
            job.finished_at = time.time()
            self._save(job)
            swept.append(job.job_id)
        return swept

    def _save(self, job: IngestionJob) -> None:
        payload = json.dumps(_job_to_dict(job), ensure_ascii=False)
        self._db.execute(
            "INSERT OR REPLACE INTO ingestion_jobs (job_id, data, updated_at) VALUES (?, ?, ?)",
            (job.job_id, payload, time.time()),
        )


def _job_to_dict(job: IngestionJob) -> dict:
    data = asdict(job)
    for file, domain_file in zip(data["files"], job.files, strict=True):
        file["status"] = domain_file.status.value
    return data


def _job_from_row(row: object) -> IngestionJob | None:
    if row is None:
        return None
    data = json.loads(row["data"])  # type: ignore[index]
    return IngestionJob(
        job_id=data["job_id"],
        files=[
            FileProgress(
                filename=f["filename"],
                status=FileStatus(f["status"]),
                document_id=f["document_id"],
                chunk_count=f["chunk_count"],
                error=f["error"],
            )
            for f in data["files"]
        ],
        created_at=data["created_at"],
        started_at=data["started_at"],
        finished_at=data["finished_at"],
    )


def run_ingestion_job(
    *,
    job_store: JobStore,
    job_id: str,
    store: VectorStore,
    embed_model: BaseEmbedding,
    files: list[tuple[str, bytes]],
    chunk_size: int,
    chunk_overlap: int,
) -> None:
    """Process one job's files in order, in a background thread (ADR-17).

    Delegates each file to `engine.ingest_file` unchanged — the same load/parse/chunk/
    index path, the same per-file compensation (ADR-7), the same dedup (ADR-3) as
    before this module existed. Only the bookkeeping (job/file progress) is new.

    A cancellation request (`JobStore.request_cancel`) is checked before each
    not-yet-started file; a file already mid-embedding always finishes normally —
    cancellation only ever shortens the *remaining* queue, the same rule the UI's
    "Cancel remaining" affordance already documented before this module existed.
    """
    with _ingestion_lock:
        job_store.mark_started(job_id)
        for index, (filename, content) in enumerate(files):
            if job_store.is_cancelled(job_id):
                job_store.mark_skipped(job_id, index)
                continue
            job_store.mark_processing(job_id, index)
            outcome = engine.ingest_file(
                store=store,
                embed_model=embed_model,
                filename=filename,
                content=content,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            job_store.record_outcome(job_id, index, outcome)
        job_store.mark_finished(job_id)
