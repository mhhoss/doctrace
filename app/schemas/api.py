"""Public API request/response contracts.

Independent of FastAPI and of internal domain/storage types (`indexer.IngestOutcome`,
`vector_store.DocumentSummary`, `generator.Citation`/`GeneratedAnswer`, ...): this module
defines the wire contract only, not how it is served. `api/routes.py` will be
responsible for translating between the two — a change here is a deliberate API change,
not a side effect of refactoring `rag/` or `storage/`.

Where a schema mirrors a domain type closely, it deliberately keeps the same field names
and, for `IngestOutcome`/`IngestStatus`/`Citation`/`DocumentSummary`, the same class name
as its domain counterpart — this makes the future translation in `api/routes.py`
mechanical, and the duplication is intentional, not an oversight. Top-level response
bodies are always suffixed `Response`; nested/shared item types are not.

No request schema is defined for file ingestion: an upload has no fields beyond the
files themselves (multipart form data, handled by the route layer directly), so there is
nothing here to validate ahead of the file content itself.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class IngestStatus(StrEnum):
    """Per-file outcome of an ingestion request (R-09). Mirrors `indexer.IngestStatus`."""

    INDEXED = "indexed"
    ALREADY_INDEXED = "already_indexed"
    FAILED = "failed"


class IngestOutcome(BaseModel):
    """Result for one uploaded file. Mirrors `indexer.IngestOutcome`.

    `document_id` is `None` only when identity could never be established — the file's
    extension was unsupported or it could not be parsed at all. `error` is set if and
    only if `status` is `FAILED`; `chunk_count` is positive only if `status` is
    `INDEXED` (R-09: a re-upload of already-indexed content embeds nothing new).
    """

    filename: str = Field(min_length=1)
    status: IngestStatus
    document_id: str | None = None
    chunk_count: int = Field(default=0, ge=0)
    error: str | None = None

    @model_validator(mode="after")
    def _status_is_consistent_with_error_and_chunk_count(self) -> IngestOutcome:
        if self.status is IngestStatus.FAILED:
            if not self.error:
                raise ValueError("error is required when status is FAILED")
        elif self.error is not None:
            raise ValueError("error must be unset unless status is FAILED")

        if self.status is not IngestStatus.INDEXED and self.chunk_count != 0:
            raise ValueError("chunk_count must be 0 unless status is INDEXED")
        return self


class IngestionResponse(BaseModel):
    """Response for an upload request covering one or more files (R-01, R-09).

    Always HTTP 200: one file failing to ingest is data in `results`, never a
    request-level error — the request itself was processed successfully, and a
    well-formed request can legitimately report every file as `FAILED`.
    """

    results: list[IngestOutcome]


class JobFileStatus(StrEnum):
    """A file's progress within an ingestion job (ADR-17). Mirrors `jobs.FileStatus`.

    `INDEXED`/`ALREADY_INDEXED`/`FAILED` are exactly `IngestStatus`'s members — once a
    file reaches one of them, its entry carries the same `document_id`/`chunk_count`/
    `error` rules `IngestOutcome` already documents. `QUEUED`/`PROCESSING` are job-only
    states. `SKIPPED` is also job-only: the file's turn never came because
    cancellation (`DELETE /documents/jobs/{job_id}`) was requested first.
    """

    QUEUED = "queued"
    PROCESSING = "processing"
    INDEXED = "indexed"
    ALREADY_INDEXED = "already_indexed"
    FAILED = "failed"
    SKIPPED = "skipped"


class JobFileProgress(BaseModel):
    """One file's current state within an ingestion job. Mirrors `jobs.FileProgress`."""

    filename: str = Field(min_length=1)
    status: JobFileStatus
    document_id: str | None = None
    chunk_count: int = Field(default=0, ge=0)
    error: str | None = None


class JobStatus(StrEnum):
    """An ingestion job's lifecycle stage (ADR-17). Mirrors `jobs.JobStatus`."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"


class IngestionJobResponse(BaseModel):
    """Progress and, once finished, results of a background ingestion job (ADR-17).

    Returned immediately (HTTP 202) by `POST /documents`, and again by
    `GET /documents/jobs/{job_id}` on every poll — same shape either way, so a client
    reads `status`/`completed`/`total`/`current_filename`/`eta_seconds` at any point in
    the job's life without a different response type to handle once it finishes.
    `eta_seconds` is `None` whenever there isn't yet a real completed file to base an
    estimate on (no fake progress).
    """

    job_id: str = Field(min_length=1)
    status: JobStatus
    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    current_filename: str | None = None
    eta_seconds: float | None = None
    files: list[JobFileProgress]


class DocumentSummary(BaseModel):
    """One entry in the knowledge base listing (R-06). Mirrors `vector_store.DocumentSummary`."""

    document_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    file_type: str = Field(min_length=1)
    chunk_count: int = Field(ge=1)  # invariant 8: a listed document is never partial


class DocumentListResponse(BaseModel):
    """Response for listing the knowledge base (R-06)."""

    documents: list[DocumentSummary]


class DeleteDocumentResponse(BaseModel):
    """Result of deleting one document by id (R-07).

    Deletion is idempotent (`vector_store.delete_document` is a no-op if the document
    is absent): `deleted` is `False` to report that nothing was there to remove, not to
    signal an error.
    """

    document_id: str = Field(min_length=1)
    deleted: bool


class ResetResponse(BaseModel):
    """Result of clearing the entire knowledge base (R-07)."""

    message: str = "Knowledge base has been reset."


class QueryRequest(BaseModel):
    """A natural-language question (R-04) — English, Persian, or mixed (R-10).

    Rejected if blank after stripping whitespace; whether that text is answerable is a
    retrieval/generation concern (ADR-4), not a request-validation one.
    """

    query: str = Field(min_length=1)

    @model_validator(mode="after")
    def _query_is_not_blank(self) -> QueryRequest:
        if not self.query.strip():
            raise ValueError("query must not be blank")
        return self


class Citation(BaseModel):
    """One supporting source for an answer (R-05). Mirrors `generator.Citation`."""

    document_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    file_type: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    excerpt: str


class AnswerResponse(BaseModel):
    """Response to a question: a grounded answer, or a deterministic refusal (ADR-4).

    `sources` is empty if and only if `is_refusal` is true (groundedness rule 4): a
    refusal has nothing to cite, and every real answer must cite something.
    """

    answer: str
    sources: list[Citation]
    is_refusal: bool

    @model_validator(mode="after")
    def _sources_present_iff_not_a_refusal(self) -> AnswerResponse:
        if self.is_refusal and self.sources:
            raise ValueError("a refusal must not carry sources")
        if not self.is_refusal and not self.sources:
            raise ValueError("a non-refusal answer must carry at least one source")
        return self


class ProviderSummary(BaseModel):
    """One configured provider, safe to display. Mirrors `config.ProviderDescription`.

    `masked_key` is never the credential itself — the API has no endpoint that returns
    a usable secret, by construction. `base_url` carries no credential, so it is shown
    in full rather than reduced to `host`, to pre-fill a runtime-edit form.
    """

    model: str = Field(min_length=1)
    host: str
    base_url: str
    masked_key: str = Field(min_length=1)
    is_local: bool


class SettingsResponse(BaseModel):
    """The provider configuration currently in effect.

    Reflects the active runtime configuration (R-08): the environment/`.env` values
    resolved at startup, unless overridden since by `POST /settings/llm` or
    `POST /settings/embedding` (ADR-10's amendment) — this endpoint itself only reads
    and reports whatever is currently active, it never changes anything.
    """

    llm: ProviderSummary
    embedding: ProviderSummary


class ConnectionCheck(BaseModel):
    """Outcome of one real provider call. `detail` is set only when `ok` is false."""

    ok: bool
    detail: str | None = None


class ConnectionTestResponse(BaseModel):
    """Result of probing both configured providers.

    Always 200: an unreachable provider is the answer to the question being asked, not
    a failure of the request itself.
    """

    llm: ConnectionCheck
    embedding: ConnectionCheck


class ErrorResponse(BaseModel):
    """Structured envelope for a request-level error (e.g. 404 document not found).

    This is the shape for errors this API raises deliberately; it does not replace
    FastAPI's own validation-error body for malformed requests (a different, framework-
    owned shape), which `api/routes.py` will need to reconcile.
    """

    detail: str = Field(min_length=1)


class HealthCheck(BaseModel):
    """One dependency's status (ADR-26): a missing binary, a missing pinned model
    file, or an unwritable storage path each fail loudly here instead of surfacing
    later as an opaque 500 on first real use."""

    name: str = Field(min_length=1)
    ok: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    checks: list[HealthCheck]


class ExtractedItem(BaseModel):
    """One obligation/requirement, page-cited (ADR-25). Mirrors `extraction.models.ExtractedItem`."""

    item_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    category: str
    text: str
    quote: str
    section_label: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    located: bool
    confidence: float = Field(ge=0.0, le=1.0)
    verification_status: str
    verification_note: str | None = None


class SectionOutcome(BaseModel):
    """One section's extraction result — visible so a partial-failure document (some
    sections failed, others succeeded) is legible, not just its surviving items."""

    section_id: str = Field(min_length=1)
    label: str | None = None
    status: str
    item_count: int = Field(ge=0)
    error: str | None = None


class ExtractionResponse(BaseModel):
    """One document's full extraction result (R-09-style: never raises, reports a
    failure as data)."""

    document_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    status: str
    items: list[ExtractedItem] = Field(default_factory=list)
    sections: list[SectionOutcome] = Field(default_factory=list)
    error: str | None = None
