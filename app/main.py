"""FastAPI application construction and wiring. No business logic (ARCHITECTURE.md).

Settings, the embedding client, and the LLM client are built once here, in `lifespan`,
and handed to a `ProviderRegistry` stored on `app.state` — routes only ever read that
registry (`api/routes.py`'s dependencies), never construct a client themselves.
Provider configuration is resolved once at startup and never mutated afterward
(ADR-26 removed the runtime provider-swap endpoints this registry used to support);
the `VectorStore` is likewise a true singleton, never rebuilt or swapped. Opening the
store here is also where the ADR-8 embedding-fingerprint check runs, and a mismatch
fails application startup rather than surfacing per request.

Jobs, extraction results, and original files are backed by one SQLite DB under
`Settings.data_dir` (ADR-29) — a restart no longer loses them.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import (
    ProviderRegistry,
    Settings,
    build_embedding_model,
    build_llm,
    describe_providers,
    get_settings,
    require_credentials,
)
from app.extraction.store import ExtractionStore
from app.observability import configure_logging, log_event
from app.rag.jobs import JobStore
from app.storage.db import Database
from app.storage.files import FileStore
from app.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)


def _lifespan_for(settings_factory: Callable[[], Settings]):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = settings_factory()
        configure_logging(settings.log_level)
        embed_model = build_embedding_model(settings)
        llm = build_llm(settings)
        # Routes read `app.state.registry`, never construct a client themselves
        # (ADR-10); its contents are set once here and never mutated afterward (ADR-26).
        app.state.registry = ProviderRegistry(
            settings=settings, llm=llm, embed_model=embed_model
        )
        if not settings.api_key:
            log_event(
                logger,
                logging.WARNING,
                "starting with no API_KEY — every mutating endpoint is unauthenticated",
                recommendation="only run this on a network you fully trust",
            )
        # Constructing VectorStore performs the ADR-8 fingerprint check; letting
        # EmbeddingMismatchError propagate here fails startup instead of leaving the
        # app to serve requests against an index it cannot safely read or write. The
        # store itself is never swapped or rebuilt at runtime — only the registry above
        # is mutable.
        app.state.store = VectorStore(
            path=settings.chroma_path,
            collection_name=settings.chroma_collection,
            embedding_fingerprint=settings.embedding_fingerprint,
        )
        # One SQLite DB backs jobs, extraction results, and original files (ADR-29).
        db = Database(settings.data_dir / "doctrace.db")
        app.state.db = db
        app.state.file_store = FileStore(db)
        app.state.job_store = JobStore(db)
        swept = app.state.job_store.sweep_interrupted()
        if swept:
            log_event(
                logger,
                logging.WARNING,
                "closed out jobs interrupted by a prior restart",
                job_ids=swept,
            )
        app.state.extraction_store = ExtractionStore(db)
        llm_provider, embedding_provider = describe_providers(settings)
        log_event(
            logger,
            logging.INFO,
            "startup complete",
            llm_model=llm_provider.model,
            llm_host=llm_provider.host,
            embedding_model=embedding_provider.model,
            embedding_host=embedding_provider.host,
            embedding_is_local=embedding_provider.is_local,
        )
        yield
        db.close()

    return lifespan


def _settings_from_environment() -> Settings:
    """The real startup path: load from the environment and fail loudly if the
    provider credentials a test never needs (stub providers) are actually blank."""
    settings = get_settings()
    require_credentials(settings)
    return settings


def create_app(*, settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. `settings` overrides the environment, for tests.

    Credential validation only runs when `settings` is omitted — tests that inject
    `Settings` directly (with stub embedding/LLM clients that never make a real
    provider call) are intentionally exempt.
    """
    settings_factory = (
        _settings_from_environment if settings is None else (lambda: settings)
    )
    app = FastAPI(
        title="DocTrace",
        lifespan=_lifespan_for(settings_factory),
    )
    app.include_router(router)
    # The built React app (web/dist, `npm run build` in web/), mounted after the API
    # router so an API path always resolves first — this is registration order, not a
    # path-prefix distinction, since the SPA is mounted at "/" too (ADR-28). Absent in
    # dev-against-source or a checkout that never ran the frontend build; skipped
    # rather than failing startup, since the API is fully usable without it.
    web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


app = create_app()
