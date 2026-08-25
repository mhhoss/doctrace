"""Thin HTTP layer: validate, delegate to `rag/engine.py` and `storage/`, return schemas.

No chunking, retrieval, generation, or indexing logic lives here (invariant: "Routes
contain no chunking, retrieval, or generation logic", ARCHITECTURE.md Layers). This
module's only job is translating between the wire contracts in `schemas/api.py` and the
internal domain/storage types (`indexer.IngestOutcome`, `vector_store.DocumentSummary`,
`generator.Citation`/`GeneratedAnswer`) — never exposing the latter directly.

Dependency providers (`_get_settings`/`_get_vector_store`/`_get_embed_model`/`_get_llm`/
`_get_registry`) read `request.app.state` — the settings/embedding/LLM trio through the
`ProviderRegistry` `app/main.py` builds at startup, the `VectorStore` directly — and
never construct their own (ADR-10). Provider configuration is env/config-file-only,
resolved once at startup; there is no runtime provider-swap endpoint (ADR-26 removed
the two that used to exist here — a request-supplied `base_url` accepted from any
unauthenticated caller). Tests that mount this router directly (no `app/main.py`
lifespan) override these dependencies to inject stubs instead.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from openai import APIError

from app.config import (
    ProviderDescription,
    ProviderRegistry,
    Settings,
    describe_providers,
    probe_embedding,
    probe_llm,
)
from app.observability import log_event
from app.rag import engine
from app.rag.generator import Citation as DomainCitation
from app.rag.generator import GeneratedAnswer
from app.rag.indexer import IngestOutcome as DomainIngestOutcome
from app.rag.jobs import IngestionJob as DomainIngestionJob
from app.rag.jobs import JobStore, run_ingestion_job
from app.schemas import api as schemas
from app.storage.vector_store import DocumentSummary as DomainDocumentSummary
from app.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from llama_index.core.base.embeddings.base import BaseEmbedding
    from llama_index.core.llms import LLM

router = APIRouter()


# --- dependencies ---
# `_get_vector_store` reads the one `VectorStore` `app/main.py`'s `lifespan` builds at
# startup and never rebuilds — opening it there is also where the ADR-8 embedding
# fingerprint check runs, so a mismatch fails startup rather than this layer having to
# translate it into a response. The other three read the `ProviderRegistry` that same
# `lifespan` builds — a `ProviderRegistry` still exists as an internal type (built once
# at startup, never mutated after ADR-26), kept because several routes read through it.


def _get_registry(request: Request) -> ProviderRegistry:
    return request.app.state.registry


def _get_settings(request: Request) -> Settings:
    return request.app.state.registry.settings


def require_api_key(
    request: Request, settings: Settings = Depends(_get_settings)
) -> None:
    """Gate mutating routes behind `X-API-Key` when `API_KEY` is configured (ADR-26).

    A no-op when `API_KEY` is unset — that is an explicit choice for a single-user
    local deployment kept off any untrusted network, not an oversight; `app/main.py`
    logs a startup warning in that case so the operator sees the trade-off being made.
    """
    if not settings.api_key:
        return
    if request.headers.get("x-api-key") != settings.api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid API key.")


def _get_vector_store(request: Request) -> VectorStore:
    return request.app.state.store


def _get_job_store(request: Request) -> JobStore:
    return request.app.state.job_store


def _get_embed_model(request: Request) -> BaseEmbedding:
    return request.app.state.registry.embed_model


def _get_llm(request: Request) -> LLM:
    return request.app.state.registry.llm


# --- domain -> schema translation ---


def _to_schema_outcome(outcome: DomainIngestOutcome) -> schemas.IngestOutcome:
    return schemas.IngestOutcome(
        filename=outcome.filename,
        status=schemas.IngestStatus(outcome.status.value),
        document_id=outcome.document_id,
        chunk_count=outcome.chunk_count,
        error=outcome.error,
    )


def _to_schema_summary(summary: DomainDocumentSummary) -> schemas.DocumentSummary:
    return schemas.DocumentSummary(
        document_id=summary.document_id,
        filename=summary.filename,
        file_type=summary.file_type,
        chunk_count=summary.chunk_count,
    )


def _to_schema_citation(citation: DomainCitation) -> schemas.Citation:
    return schemas.Citation(
        document_id=citation.document_id,
        filename=citation.filename,
        file_type=citation.file_type,
        chunk_id=citation.chunk_id,
        excerpt=citation.excerpt,
    )


def _to_schema_job(job: DomainIngestionJob) -> schemas.IngestionJobResponse:
    return schemas.IngestionJobResponse(
        job_id=job.job_id,
        status=schemas.JobStatus(job.status.value),
        total=job.total,
        completed=job.completed_count,
        current_filename=job.current_filename,
        eta_seconds=job.eta_seconds,
        files=[
            schemas.JobFileProgress(
                filename=f.filename,
                status=schemas.JobFileStatus(f.status.value),
                document_id=f.document_id,
                chunk_count=f.chunk_count,
                error=f.error,
            )
            for f in job.files
        ],
    )


def _to_schema_provider(provider: ProviderDescription) -> schemas.ProviderSummary:
    return schemas.ProviderSummary(
        model=provider.model,
        host=provider.host,
        base_url=provider.base_url,
        masked_key=provider.masked_key,
        is_local=provider.is_local,
    )


def _to_schema_answer(answer: GeneratedAnswer) -> schemas.AnswerResponse:
    return schemas.AnswerResponse(
        answer=answer.answer,
        sources=[_to_schema_citation(source) for source in answer.sources],
        is_refusal=answer.is_refusal,
    )


# --- routes ---


@router.post(
    "/documents",
    response_model=schemas.IngestionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_api_key)],
)
async def ingest_documents(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    settings: Settings = Depends(_get_settings),
    store: VectorStore = Depends(_get_vector_store),
    embed_model: BaseEmbedding = Depends(_get_embed_model),
    job_store: JobStore = Depends(_get_job_store),
) -> schemas.IngestionJobResponse:
    """Start a background ingestion job for one or more files (R-01, R-09, ADR-17).

    Returns immediately with the job's initial (`queued`) state — embedding runs in a
    background thread via `rag.jobs.run_ingestion_job`, never inline in this request,
    so the API stays responsive while it works. Poll `GET /documents/jobs/{job_id}`
    for progress and, once `status` is `completed`, the same per-file
    `indexed`/`already_indexed`/`failed` outcomes the old synchronous response carried
    (ADR-7's per-file compensation and ADR-3's dedup are unchanged — only *when* the
    caller learns the outcome changed, not what it is).
    """
    loaded = [((file.filename or "unnamed"), await file.read()) for file in files]
    max_bytes = settings.max_upload_mb * 1024 * 1024
    oversized = [name for name, content in loaded if len(content) > max_bytes]
    if oversized:
        raise HTTPException(
            status_code=413,
            detail=f"File(s) exceed the {settings.max_upload_mb}MB limit: "
            f"{', '.join(oversized)}.",
        )
    job = job_store.create(filenames=[name for name, _ in loaded])
    background_tasks.add_task(
        run_ingestion_job,
        job_store=job_store,
        job_id=job.job_id,
        store=store,
        embed_model=embed_model,
        files=loaded,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    return _to_schema_job(job)


@router.get("/documents/jobs/{job_id}", response_model=schemas.IngestionJobResponse)
def get_ingestion_job(
    job_id: str,
    job_store: JobStore = Depends(_get_job_store),
) -> schemas.IngestionJobResponse:
    """Poll one ingestion job's progress and, once finished, its results (ADR-17).

    404 if `job_id` is unknown — including after an API restart, since the job store
    is in-memory only (see `rag/jobs.py`'s module docstring for why that is safe).
    """
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail=f"No ingestion job found with id {job_id!r}."
        )
    return _to_schema_job(job)


@router.delete(
    "/documents/jobs/{job_id}",
    response_model=schemas.IngestionJobResponse,
    dependencies=[Depends(require_api_key)],
)
def cancel_ingestion_job(
    job_id: str,
    job_store: JobStore = Depends(_get_job_store),
) -> schemas.IngestionJobResponse:
    """Request cancellation of a job's not-yet-started files (ADR-17).

    Idempotent and best-effort: a file already mid-embedding always finishes normally
    (`run_ingestion_job` only checks this before starting the *next* file), and
    requesting cancellation on an already-completed or unknown job is a 404, not an
    error to retry differently.
    """
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail=f"No ingestion job found with id {job_id!r}."
        )
    job_store.request_cancel(job_id)
    return _to_schema_job(job_store.get(job_id) or job)


@router.get("/documents", response_model=schemas.DocumentListResponse)
def list_documents(
    store: VectorStore = Depends(_get_vector_store),
) -> schemas.DocumentListResponse:
    """List indexed documents (R-06). Derived entirely from store metadata (ADR-2)."""
    return schemas.DocumentListResponse(
        documents=[_to_schema_summary(doc) for doc in store.list_documents()]
    )


@router.delete(
    "/documents/{document_id}",
    response_model=schemas.DeleteDocumentResponse,
    dependencies=[Depends(require_api_key)],
)
def delete_document(
    document_id: str,
    store: VectorStore = Depends(_get_vector_store),
) -> schemas.DeleteDocumentResponse:
    """Delete one document by id (R-07). Idempotent: absence is not an error."""
    existed = store.document_exists(document_id)
    if existed:
        store.delete_document(document_id)
    return schemas.DeleteDocumentResponse(document_id=document_id, deleted=existed)


@router.post(
    "/reset",
    response_model=schemas.ResetResponse,
    dependencies=[Depends(require_api_key)],
)
def reset_knowledge_base(
    store: VectorStore = Depends(_get_vector_store),
) -> schemas.ResetResponse:
    """Clear the entire knowledge base (R-07)."""
    store.reset()
    return schemas.ResetResponse()


@router.post(
    "/query",
    response_model=schemas.AnswerResponse,
    responses={502: {"model": schemas.ErrorResponse}},
    dependencies=[Depends(require_api_key)],
)
def query(
    request: schemas.QueryRequest,
    settings: Settings = Depends(_get_settings),
    store: VectorStore = Depends(_get_vector_store),
    embed_model: BaseEmbedding = Depends(_get_embed_model),
    llm: LLM = Depends(_get_llm),
) -> schemas.AnswerResponse:
    """Answer a question from indexed documents only (R-04, R-05), or refuse (ADR-4).

    A provider/network failure during embedding or generation surfaces as a 502 with
    `schemas.ErrorResponse`, not a raw 500 — the request was valid, the configured
    provider just could not serve it.
    """
    try:
        result = engine.answer_query(
            store=store,
            embed_model=embed_model,
            llm=llm,
            query=request.query,
            top_k=settings.retrieval_top_k,
            min_score=settings.retrieval_min_score,
        )
    except APIError as error:
        log_event(
            logger,
            logging.WARNING,
            "provider request failed",
            route="/query",
            error_type=type(error).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="The configured LLM/embedding provider could not be reached. "
            "Please try again.",
        ) from error
    return _to_schema_answer(result)


@router.get("/health", response_model=schemas.HealthResponse)
def health(settings: Settings = Depends(_get_settings)) -> schemas.HealthResponse:
    """Real dependency status, not just "the process is running" (ADR-26).

    Deliberately does not probe the LLM/embedding provider with a live call (that is
    `POST /settings/test`'s job, and is slow/costly to run on every health poll) —
    this checks only local, cheap-to-verify preconditions: the `pdftotext` binary PDF
    parsing depends on, and, for `onnx_local`, that the pinned model files this
    project ships a manifest for actually exist on disk.
    """
    checks = [
        schemas.HealthCheck(
            name="poppler",
            ok=shutil.which("pdftotext") is not None,
            detail=None if shutil.which("pdftotext") else "pdftotext not found on PATH",
        )
    ]
    if settings.embedding_provider == "onnx_local":
        model_dir = settings.embedding_onnx_model_dir
        missing = [
            name
            for name in ("model_uint8.onnx", "tokenizer.json")
            if not (model_dir / name).is_file()
        ]
        checks.append(
            schemas.HealthCheck(
                name="onnx_model_artifact",
                ok=not missing,
                detail=None if not missing else f"missing under {model_dir}: {missing}",
            )
        )
    writable = _nearest_existing_ancestor_is_writable(settings.chroma_path)
    checks.append(
        schemas.HealthCheck(
            name="chroma_path_writable",
            ok=writable,
            detail=None if writable else f"{settings.chroma_path} is not writable",
        )
    )
    return schemas.HealthResponse(ok=all(c.ok for c in checks), checks=checks)


def _nearest_existing_ancestor_is_writable(path: Path) -> bool:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return os.access(candidate, os.W_OK)
    return False


@router.get("/settings", response_model=schemas.SettingsResponse)
def read_settings(
    settings: Settings = Depends(_get_settings),
) -> schemas.SettingsResponse:
    """Report the provider configuration currently in effect, with credentials masked
    (R-08) — the environment/`.env` values resolved once at startup (ADR-26: there is
    no runtime write path for this anymore). This route never changes anything.
    """
    llm_provider, embedding_provider = describe_providers(settings)
    return schemas.SettingsResponse(
        llm=_to_schema_provider(llm_provider),
        embedding=_to_schema_provider(embedding_provider),
    )


@router.post("/settings/test", response_model=schemas.ConnectionTestResponse)
def test_providers(
    embed_model: BaseEmbedding = Depends(_get_embed_model),
    llm: LLM = Depends(_get_llm),
) -> schemas.ConnectionTestResponse:
    """Probe both configured providers with one real call each.

    Always 200: "the provider is unreachable" is the result being requested, not an
    error in the request, so it is reported in the body like an ingestion outcome.
    """
    return schemas.ConnectionTestResponse(
        llm=_check(lambda: probe_llm(llm)),
        embedding=_check(lambda: probe_embedding(embed_model)),
    )


def _check(probe: Callable[[], None]) -> schemas.ConnectionCheck:
    try:
        probe()
    except Exception as error:  # noqa: BLE001 - any provider failure is a failed check
        return schemas.ConnectionCheck(
            ok=False, detail=str(error) or type(error).__name__
        )
    return schemas.ConnectionCheck(ok=True)
