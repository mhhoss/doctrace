"""Background ingestion job tracking (ADR-17, ADR-29): `JobStore`, `IngestionJob`
status/ETA derivation, cancellation, and restart recovery — all pure data/logic, no
FastAPI, no real embedding or vector store. `run_ingestion_job`'s wiring into a real
ingestion pipeline is exercised through `tests/integration/test_routes.py`'s job
endpoints instead.
"""

from __future__ import annotations

import time

from app.rag.indexer import IngestOutcome, IngestStatus
from app.rag.jobs import FileStatus, IngestionJob, JobStatus, JobStore
from app.storage.db import Database


def _require(store: JobStore, job_id: str) -> IngestionJob:
    """`JobStore.get` returns `IngestionJob | None`; every call site here already
    knows the job exists, so this narrows the type instead of asserting inline
    everywhere."""
    job = store.get(job_id)
    assert job is not None
    return job


class TestJobCreation:
    def test_create_returns_a_job_with_every_file_queued(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf", "b.pdf"])

        assert job.total == 2
        assert job.status is JobStatus.QUEUED
        assert [f.status for f in job.files] == [FileStatus.QUEUED, FileStatus.QUEUED]
        assert job.completed_count == 0
        assert job.current_filename is None
        assert job.eta_seconds is None

    def test_get_survives_a_fresh_store_over_the_same_db(self, db: Database) -> None:
        """The whole point of ADR-29: a job outlives the `JobStore` instance that
        created it, as long as the underlying `Database` is the same."""
        created = JobStore(db).create(["a.pdf"])
        assert JobStore(db).get(created.job_id) == created

    def test_get_unknown_id_returns_none(self, db: Database) -> None:
        assert JobStore(db).get("does-not-exist") is None


class TestJobStatusDerivation:
    def test_status_is_queued_before_started(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf"])
        assert job.status is JobStatus.QUEUED

    def test_status_is_running_once_started(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf"])
        store.mark_started(job.job_id)
        assert _require(store, job.job_id).status is JobStatus.RUNNING

    def test_status_is_completed_once_finished(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf"])
        store.mark_started(job.job_id)
        store.mark_finished(job.job_id)
        assert _require(store, job.job_id).status is JobStatus.COMPLETED


class TestFileProgress:
    def test_mark_processing_updates_only_that_file(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf", "b.pdf"])
        store.mark_processing(job.job_id, 0)

        updated = _require(store, job.job_id)
        assert updated.files[0].status is FileStatus.PROCESSING
        assert updated.files[1].status is FileStatus.QUEUED
        assert updated.current_filename == "a.pdf"

    def test_record_outcome_mirrors_the_real_ingest_outcome(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf"])
        outcome = IngestOutcome(
            filename="a.pdf",
            status=IngestStatus.INDEXED,
            document_id="doc-1",
            chunk_count=3,
        )
        store.record_outcome(job.job_id, 0, outcome)

        file = _require(store, job.job_id).files[0]
        assert file.status is FileStatus.INDEXED
        assert file.document_id == "doc-1"
        assert file.chunk_count == 3
        assert file.error is None

    def test_record_outcome_carries_a_failure_reason(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["bad.pdf"])
        outcome = IngestOutcome.failure(filename="bad.pdf", error="No extractable text.")
        store.record_outcome(job.job_id, 0, outcome)

        file = _require(store, job.job_id).files[0]
        assert file.status is FileStatus.FAILED
        assert file.error == "No extractable text."

    def test_completed_count_counts_every_terminal_status(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf", "b.pdf", "c.pdf"])
        store.record_outcome(
            job.job_id, 0, IngestOutcome(filename="a.pdf", status=IngestStatus.INDEXED)
        )
        store.mark_skipped(job.job_id, 1)

        updated = _require(store, job.job_id)
        assert updated.completed_count == 2
        assert updated.files[1].status is FileStatus.SKIPPED
        assert updated.files[1].error is None


class TestEtaSeconds:
    def test_eta_is_none_before_the_job_starts(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf"])
        assert job.eta_seconds is None

    def test_eta_is_none_until_a_file_actually_completes(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf", "b.pdf"])
        store.mark_started(job.job_id)
        store.mark_processing(job.job_id, 0)
        assert _require(store, job.job_id).eta_seconds is None

    def test_eta_is_none_once_the_job_is_fully_complete(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf"])
        store.mark_started(job.job_id)
        store.record_outcome(
            job.job_id, 0, IngestOutcome(filename="a.pdf", status=IngestStatus.INDEXED)
        )
        store.mark_finished(job.job_id)
        assert _require(store, job.job_id).eta_seconds is None

    def test_eta_extrapolates_only_from_real_completed_file_timing(
        self, db: Database
    ) -> None:
        """Not simulated: `avg(elapsed / completed) * remaining`, using this job's own
        real timestamps — never a placeholder or an assumed rate."""
        job = JobStore(db).create(["a.pdf", "b.pdf", "c.pdf"])
        job.started_at = time.time() - 10.0  # ~10s of real elapsed job time
        job.files[0].status = FileStatus.INDEXED  # one file done so far

        # One ~10s sample for the one completed file, two remaining -> ~20s. A small
        # tolerance covers the real time that elapses while this test itself runs.
        assert job.eta_seconds is not None
        assert 19.0 < job.eta_seconds < 21.0


class TestCancellation:
    def test_request_cancel_on_a_running_job_returns_true(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf"])
        store.mark_started(job.job_id)
        assert store.request_cancel(job.job_id) is True
        assert store.is_cancelled(job.job_id) is True

    def test_request_cancel_on_an_unknown_job_returns_false(self, db: Database) -> None:
        assert JobStore(db).request_cancel("does-not-exist") is False

    def test_request_cancel_on_a_finished_job_returns_false(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf"])
        store.mark_started(job.job_id)
        store.mark_finished(job.job_id)
        assert store.request_cancel(job.job_id) is False

    def test_is_cancelled_defaults_to_false(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf"])
        assert store.is_cancelled(job.job_id) is False


class TestSweepInterrupted:
    """`sweep_interrupted` (ADR-29): run once at startup to close out jobs a prior
    process crash left stuck mid-run."""

    def test_a_started_unfinished_job_is_marked_failed_and_closed(
        self, db: Database
    ) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf", "b.pdf"])
        store.mark_started(job.job_id)
        store.mark_processing(job.job_id, 0)

        swept = JobStore(db).sweep_interrupted()

        assert swept == [job.job_id]
        updated = _require(store, job.job_id)
        assert updated.status is JobStatus.COMPLETED
        assert updated.files[0].status is FileStatus.FAILED
        assert updated.files[0].error
        assert updated.files[1].status is FileStatus.FAILED

    def test_a_never_started_job_is_left_alone(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf"])

        assert JobStore(db).sweep_interrupted() == []
        assert _require(store, job.job_id).status is JobStatus.QUEUED

    def test_an_already_finished_job_is_left_alone(self, db: Database) -> None:
        store = JobStore(db)
        job = store.create(["a.pdf"])
        store.mark_started(job.job_id)
        store.record_outcome(
            job.job_id, 0, IngestOutcome(filename="a.pdf", status=IngestStatus.INDEXED)
        )
        store.mark_finished(job.job_id)

        assert JobStore(db).sweep_interrupted() == []
        assert _require(store, job.job_id).files[0].status is FileStatus.INDEXED
