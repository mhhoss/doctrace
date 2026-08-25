"""HTTP integration tests for `app/api/routes.py`.

Exercises the router through a real `TestClient`, with `StubEmbedding`/`StubLLM` and a
temporary `VectorStore` injected via `app.dependency_overrides` — never a real provider
or `chroma_db/`. `app/main.py` does not exist yet, so each test builds its own minimal
`FastAPI` app around the router, the way `main.py` eventually will.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import APIConnectionError

from app.api import routes
from app.config import Settings
from app.extraction import llm_calls
from app.extraction.models import ExtractedItemLLM, SectionExtractionResult
from app.extraction.store import ExtractionStore
from app.rag.jobs import JobStore
from app.storage.vector_store import VectorStore
from tests.conftest import (
    StubEmbedding,
    StubLLM,
    log_fields,
    settings_kwargs,
)
from tests.docx_fixtures import build_docx
from tests.pdf_fixtures import build_pdf

CHUNK_SIZE = 200
CHUNK_OVERLAP = 30

ENGLISH_TEXT = "Kubernetes cluster costs increased significantly this quarter."
PERSIAN_TEXT = "هزینه خوشه کوبرنتیز دوازده درصد افزایش یافت."


def _settings(**overrides: object) -> Settings:
    return Settings(
        **settings_kwargs(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            retrieval_top_k=5,
            retrieval_min_score=0.0,
            **overrides,
        )
    )


@pytest.fixture
def app(store: VectorStore, embed_model: StubEmbedding, llm: StubLLM) -> FastAPI:
    application = FastAPI()
    application.include_router(routes.router)
    application.dependency_overrides[routes._get_settings] = lambda: _settings()
    application.dependency_overrides[routes._get_vector_store] = lambda: store
    application.dependency_overrides[routes._get_embed_model] = lambda: embed_model
    application.dependency_overrides[routes._get_llm] = lambda: llm
    job_store = JobStore()
    application.dependency_overrides[routes._get_job_store] = lambda: job_store
    extraction_store = ExtractionStore()
    application.dependency_overrides[routes._get_extraction_store] = (
        lambda: extraction_store
    )
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def upload(client: TestClient, files: list[tuple[str, bytes]]) -> httpx.Response:
    """POST /documents (ADR-17): starts a background ingestion job and returns its
    initial `202` response — `job_id`/`status`, not yet the per-file results."""
    return client.post(
        "/documents",
        files=[("files", (name, content)) for name, content in files],
    )


def upload_and_finish(client: TestClient, files: list[tuple[str, bytes]]) -> dict:
    """Upload and return the job's finished state.

    `TestClient` runs the whole ASGI cycle for a request in-process, including its
    `BackgroundTasks` (the same behavior FastAPI's own docs rely on to test background
    tasks synchronously) — so by the time `upload()` returns, `run_ingestion_job` has
    already completed, and one immediate poll already reports the finished job. This
    only exists to keep call sites terse; nothing here is a real timing dependency.
    """
    job_id = upload(client, files).json()["job_id"]
    return client.get(f"/documents/jobs/{job_id}").json()


class TestIngestDocuments:
    def test_upload_returns_202_with_the_jobs_initial_state(
        self, client: TestClient
    ) -> None:
        """POST /documents starts a job and returns immediately (ADR-17) — it does
        not wait for embedding, so its own response never carries a final outcome."""
        response = upload(client, [("report.pdf", build_pdf([ENGLISH_TEXT]))])

        assert response.status_code == 202
        body = response.json()
        assert body["job_id"]
        assert body["total"] == 1
        assert body["files"][0]["filename"] == "report.pdf"
        assert body["files"][0]["status"] == "queued"

    def test_a_file_over_the_configured_limit_is_rejected_with_413(
        self, store: VectorStore, embed_model: StubEmbedding, llm: StubLLM
    ) -> None:
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes._get_settings] = (
            lambda: _settings(max_upload_mb=1)
        )
        app.dependency_overrides[routes._get_vector_store] = lambda: store
        app.dependency_overrides[routes._get_embed_model] = lambda: embed_model
        app.dependency_overrides[routes._get_llm] = lambda: llm
        app.dependency_overrides[routes._get_job_store] = lambda: JobStore()

        oversized = b"x" * (2 * 1024 * 1024)
        response = TestClient(app).post(
            "/documents", files=[("files", ("huge.txt", oversized))]
        )

        assert response.status_code == 413

    def test_single_file_ingestion(self, client: TestClient) -> None:
        job = upload_and_finish(client, [("report.pdf", build_pdf([ENGLISH_TEXT]))])

        assert job["status"] == "completed"
        assert len(job["files"]) == 1
        result = job["files"][0]
        assert result["filename"] == "report.pdf"
        assert result["status"] == "indexed"
        assert result["chunk_count"] > 0
        assert result["error"] is None
        assert result["document_id"]

    def test_single_txt_file_ingestion(self, client: TestClient) -> None:
        job = upload_and_finish(client, [("گزارش.txt", PERSIAN_TEXT.encode())])

        assert job["status"] == "completed"
        result = job["files"][0]
        assert result["filename"] == "گزارش.txt"
        assert result["status"] == "indexed"
        assert result["chunk_count"] > 0
        assert result["error"] is None

    def test_multi_file_ingestion_with_partial_failure(
        self, client: TestClient
    ) -> None:
        job = upload_and_finish(
            client,
            [
                ("a.pdf", build_pdf(["Alpha document."])),
                ("notes.rtf", b"unsupported extension"),
                ("گزارش.pdf", build_pdf([PERSIAN_TEXT])),
            ],
        )

        by_name = {r["filename"]: r for r in job["files"]}
        assert by_name["a.pdf"]["status"] == "indexed"
        assert by_name["notes.rtf"]["status"] == "failed"
        assert by_name["notes.rtf"]["error"]
        assert by_name["notes.rtf"]["chunk_count"] == 0
        assert by_name["گزارش.pdf"]["status"] == "indexed"

    def test_unsupported_extension_is_a_failed_result_not_an_http_error(
        self, client: TestClient
    ) -> None:
        job = upload_and_finish(client, [("notes.rtf", b"plain text")])

        result = job["files"][0]
        assert result["status"] == "failed"
        assert result["error"]

    def test_corrupt_file_of_a_supported_type_is_a_failed_result(
        self, client: TestClient
    ) -> None:
        job = upload_and_finish(client, [("broken.pdf", b"not a real pdf")])

        result = job["files"][0]
        assert result["status"] == "failed"

    def test_a_request_where_every_file_fails_is_still_202_and_completes(
        self, client: TestClient
    ) -> None:
        job = upload_and_finish(
            client,
            [("a.rtf", b"x"), ("b.rtf", b"y")],
        )

        statuses = {r["status"] for r in job["files"]}
        assert statuses == {"failed"}

    def test_unknown_job_id_is_a_404(self, client: TestClient) -> None:
        response = client.get("/documents/jobs/does-not-exist")
        assert response.status_code == 404

    def test_cancel_requested_after_completion_still_reports_the_finished_job(
        self, client: TestClient
    ) -> None:
        """Cancelling a job that already finished is a no-op, not an error — the
        finished result stands (`JobStore.request_cancel` only affects not-yet-started
        files, and there are none left once `status` is `completed`)."""
        job_id = upload(client, [("report.pdf", build_pdf([ENGLISH_TEXT]))]).json()[
            "job_id"
        ]

        response = client.delete(f"/documents/jobs/{job_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["files"][0]["status"] == "indexed"

    def test_cancel_on_unknown_job_id_is_a_404(self, client: TestClient) -> None:
        response = client.delete("/documents/jobs/does-not-exist")
        assert response.status_code == 404


class TestApiStaysResponsiveDuringIngestion:
    """Regression test for the bug ADR-17 fixes: a slow embedding call used to block
    the entire single-worker API — `GET /documents` would hang behind an in-flight
    `POST /documents` even though it touches no embedding model at all. `StubEmbedding`
    stands in for the real, slow CPU-bound backend via `delay_seconds`."""

    def test_get_documents_returns_promptly_while_a_job_is_mid_embedding(
        self, client: TestClient, embed_model: StubEmbedding
    ) -> None:
        embed_model.delay_seconds = 2.0

        upload_response: dict[str, httpx.Response] = {}

        def do_upload() -> None:
            upload_response["response"] = upload(
                client, [("slow.pdf", build_pdf(["Some slow content."]))]
            )

        thread = threading.Thread(target=do_upload)
        thread.start()
        time.sleep(0.3)  # let the background job actually start embedding

        started = time.monotonic()
        response = client.get("/documents")
        elapsed = time.monotonic() - started

        thread.join(timeout=10)

        assert upload_response["response"].status_code == 202
        assert response.status_code == 200
        # Far under the 2s embedding delay: proof `GET /documents` was never queued
        # behind it, not just that it eventually returned.
        assert elapsed < 1.0


class TestListDocuments:
    def test_empty_knowledge_base(self, client: TestClient) -> None:
        response = client.get("/documents")

        assert response.status_code == 200
        assert response.json() == {"documents": []}

    def test_lists_ingested_documents(self, client: TestClient) -> None:
        upload(client, [("report.pdf", build_pdf([ENGLISH_TEXT]))])
        upload(client, [("گزارش.docx", build_docx([PERSIAN_TEXT]))])

        response = client.get("/documents")

        assert response.status_code == 200
        filenames = {doc["filename"] for doc in response.json()["documents"]}
        assert filenames == {"report.pdf", "گزارش.docx"}
        for doc in response.json()["documents"]:
            assert doc["chunk_count"] >= 1


class TestDeleteDocument:
    def test_deletes_an_existing_document(self, client: TestClient) -> None:
        job = upload_and_finish(client, [("report.pdf", build_pdf([ENGLISH_TEXT]))])
        document_id = job["files"][0]["document_id"]

        response = client.delete(f"/documents/{document_id}")

        assert response.status_code == 200
        assert response.json() == {"document_id": document_id, "deleted": True}
        assert client.get("/documents").json()["documents"] == []

    def test_deleting_an_absent_document_id_is_not_an_error(
        self, client: TestClient
    ) -> None:
        response = client.delete("/documents/does-not-exist")

        assert response.status_code == 200
        assert response.json() == {"document_id": "does-not-exist", "deleted": False}


class TestReset:
    def test_reset_clears_the_knowledge_base(self, client: TestClient) -> None:
        upload(client, [("report.pdf", build_pdf([ENGLISH_TEXT]))])
        upload(client, [("گزارش.docx", build_docx([PERSIAN_TEXT]))])

        response = client.post("/reset")

        assert response.status_code == 200
        assert response.json()["message"]
        assert client.get("/documents").json()["documents"] == []

    def test_reset_on_an_already_empty_knowledge_base(self, client: TestClient) -> None:
        response = client.post("/reset")

        assert response.status_code == 200


class TestQuery:
    def test_successful_grounded_query_with_citations(
        self, client: TestClient, llm: StubLLM
    ) -> None:
        upload(client, [("report.pdf", build_pdf([ENGLISH_TEXT]))])
        llm.response = "Kubernetes costs rose significantly."

        response = client.post(
            "/query", json={"query": "How did Kubernetes cluster costs change?"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["is_refusal"] is False
        assert body["answer"] == "Kubernetes costs rose significantly."
        assert len(body["sources"]) >= 1
        source = body["sources"][0]
        assert source["filename"] == "report.pdf"
        assert source["document_id"]
        assert source["excerpt"]

    def test_insufficient_context_refusal(
        self, client: TestClient, llm: StubLLM
    ) -> None:
        llm.response = "this must never be seen"

        response = client.post("/query", json={"query": "Anything at all?"})

        assert response.status_code == 200
        body = response.json()
        assert body["is_refusal"] is True
        assert body["sources"] == []
        assert body["answer"]
        assert llm.call_count == 0

    def test_blank_query_is_rejected(self, client: TestClient) -> None:
        response = client.post("/query", json={"query": "   "})

        assert response.status_code == 422

    def test_missing_query_field_is_rejected(self, client: TestClient) -> None:
        response = client.post("/query", json={})

        assert response.status_code == 422

    def test_provider_failure_returns_502_error_response(
        self, client: TestClient, llm: StubLLM
    ) -> None:
        upload(client, [("report.pdf", build_pdf([ENGLISH_TEXT]))])
        llm.error = APIConnectionError(
            request=httpx.Request("POST", "https://example.invalid")
        )

        response = client.post(
            "/query", json={"query": "How did Kubernetes cluster costs change?"}
        )

        assert response.status_code == 502
        assert response.json()["detail"]

    def test_provider_failure_is_logged(
        self, client: TestClient, llm: StubLLM, caplog: pytest.LogCaptureFixture
    ) -> None:
        upload(client, [("report.pdf", build_pdf([ENGLISH_TEXT]))])
        llm.error = APIConnectionError(
            request=httpx.Request("POST", "https://example.invalid")
        )

        with caplog.at_level(logging.WARNING, logger="app.api.routes"):
            client.post(
                "/query", json={"query": "How did Kubernetes cluster costs change?"}
            )

        record = next(r for r in caplog.records if r.name == "app.api.routes")
        assert record.getMessage() == "provider request failed"
        assert record.levelname == "WARNING"
        assert log_fields(record)["route"] == "/query"

    def test_successful_grounded_query_against_a_txt_document(
        self, client: TestClient, llm: StubLLM
    ) -> None:
        upload(client, [("notes.txt", ENGLISH_TEXT.encode())])
        llm.response = "Kubernetes costs rose significantly."

        response = client.post(
            "/query", json={"query": "How did Kubernetes cluster costs change?"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["is_refusal"] is False
        assert body["answer"] == "Kubernetes costs rose significantly."
        source = body["sources"][0]
        assert source["filename"] == "notes.txt"
        assert source["excerpt"]

    def test_persian_query_against_persian_document(
        self, client: TestClient, llm: StubLLM
    ) -> None:
        upload(client, [("گزارش.pdf", build_pdf([PERSIAN_TEXT]))])
        llm.response = "هزینه دوازده درصد افزایش یافت."

        response = client.post(
            "/query", json={"query": "هزینه خوشه کوبرنتیز چقدر تغییر کرد؟"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["is_refusal"] is False
        assert body["sources"][0]["filename"] == "گزارش.pdf"


class TestResponseSchemas:
    def test_ingestion_job_response_matches_schema_exactly(
        self, client: TestClient
    ) -> None:
        response = upload(client, [("a.pdf", build_pdf(["Some text."]))])
        body = response.json()
        assert set(body.keys()) == {
            "job_id",
            "status",
            "total",
            "completed",
            "current_filename",
            "eta_seconds",
            "files",
        }
        assert set(body["files"][0].keys()) == {
            "filename",
            "status",
            "document_id",
            "chunk_count",
            "error",
        }

    def test_document_list_response_matches_schema_exactly(
        self, client: TestClient
    ) -> None:
        upload(client, [("a.pdf", build_pdf(["Some text."]))])
        response = client.get("/documents")
        doc = response.json()["documents"][0]
        assert set(doc.keys()) == {
            "document_id",
            "filename",
            "file_type",
            "chunk_count",
        }

    def test_answer_response_matches_schema_exactly(
        self, client: TestClient, llm: StubLLM
    ) -> None:
        upload(client, [("a.pdf", build_pdf([ENGLISH_TEXT]))])
        llm.response = "answer"
        response = client.post("/query", json={"query": "Kubernetes costs"})
        body = response.json()
        assert set(body.keys()) == {"answer", "sources", "is_refusal"}
        if body["sources"]:
            assert set(body["sources"][0].keys()) == {
                "document_id",
                "filename",
                "file_type",
                "chunk_id",
                "excerpt",
            }


class TestSettingsEndpoint:
    """`GET /settings` reports provider configuration without leaking credentials."""

    def test_reports_both_providers(self, client: TestClient) -> None:
        response = client.get("/settings")
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"llm", "embedding"}
        assert set(body["llm"].keys()) == {
            "model",
            "host",
            "base_url",
            "masked_key",
            "is_local",
        }

    def test_never_returns_a_usable_credential(self, client: TestClient) -> None:
        secret = "sk-supersecretvalue-1234"
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes._get_settings] = lambda: _settings(
            llm_api_key=secret
        )
        body = TestClient(app).get("/settings").json()

        assert secret not in json.dumps(body)
        assert "•" in body["llm"]["masked_key"]

    def test_marks_a_loopback_embedding_provider_as_local(
        self, client: TestClient
    ) -> None:
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes._get_settings] = lambda: _settings(
            llm_api_key="k",
            embedding_base_url="http://127.0.0.1:11434/v1",
        )
        body = TestClient(app).get("/settings").json()

        assert body["embedding"]["is_local"] is True

    def test_marks_a_hosted_embedding_provider_as_not_local(self) -> None:
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes._get_settings] = lambda: _settings(
            llm_api_key="k",
            embedding_base_url="https://api.openai.com/v1",
        )
        body = TestClient(app).get("/settings").json()

        assert body["embedding"]["is_local"] is False
        assert body["embedding"]["host"] == "api.openai.com"


class TestConnectionTestEndpoint:
    """`POST /settings/test` makes real provider calls and reports outcomes as data."""

    def test_both_providers_reachable(self, client: TestClient) -> None:
        response = client.post("/settings/test")
        assert response.status_code == 200
        body = response.json()
        assert body["llm"] == {"ok": True, "detail": None}
        assert body["embedding"] == {"ok": True, "detail": None}

    def test_an_unreachable_llm_is_reported_as_data_not_an_error(
        self, store: VectorStore, embed_model: StubEmbedding
    ) -> None:
        broken = StubLLM()
        broken.error = RuntimeError("provider refused the connection")
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes._get_settings] = lambda: _settings()
        app.dependency_overrides[routes._get_vector_store] = lambda: store
        app.dependency_overrides[routes._get_embed_model] = lambda: embed_model
        app.dependency_overrides[routes._get_llm] = lambda: broken

        response = TestClient(app).post("/settings/test")

        assert response.status_code == 200
        body = response.json()
        assert body["llm"]["ok"] is False
        assert "refused the connection" in body["llm"]["detail"]
        assert body["embedding"]["ok"] is True



class TestApiKeyAuth:
    """`require_api_key` (ADR-26): a no-op when `API_KEY` is unset (the default), a
    hard gate on mutating routes when it is set."""

    def _app_with_api_key(
        self, store: VectorStore, embed_model: StubEmbedding, llm: StubLLM, api_key: str
    ) -> FastAPI:
        application = FastAPI()
        application.include_router(routes.router)
        application.dependency_overrides[routes._get_settings] = (
            lambda: _settings(api_key=api_key)
        )
        application.dependency_overrides[routes._get_vector_store] = lambda: store
        application.dependency_overrides[routes._get_embed_model] = lambda: embed_model
        application.dependency_overrides[routes._get_llm] = lambda: llm
        application.dependency_overrides[routes._get_job_store] = lambda: JobStore()
        application.dependency_overrides[routes._get_extraction_store] = (
            lambda: ExtractionStore()
        )
        return application

    def test_unset_api_key_leaves_mutating_routes_open(self, client: TestClient) -> None:
        """The default (no `API_KEY` configured) — every existing test in this file
        already relies on this, but it is worth pinning explicitly."""
        response = client.post("/reset")
        assert response.status_code == 200

    def test_configured_api_key_blocks_reset_without_the_header(
        self, store: VectorStore, embed_model: StubEmbedding, llm: StubLLM
    ) -> None:
        app = self._app_with_api_key(store, embed_model, llm, api_key="secret-key")
        response = TestClient(app).post("/reset")
        assert response.status_code == 401

    def test_configured_api_key_blocks_a_wrong_header_value(
        self, store: VectorStore, embed_model: StubEmbedding, llm: StubLLM
    ) -> None:
        app = self._app_with_api_key(store, embed_model, llm, api_key="secret-key")
        response = TestClient(app).post(
            "/reset", headers={"X-API-Key": "wrong-key"}
        )
        assert response.status_code == 401

    def test_configured_api_key_allows_the_matching_header(
        self, store: VectorStore, embed_model: StubEmbedding, llm: StubLLM
    ) -> None:
        app = self._app_with_api_key(store, embed_model, llm, api_key="secret-key")
        response = TestClient(app).post(
            "/reset", headers={"X-API-Key": "secret-key"}
        )
        assert response.status_code == 200

    def test_configured_api_key_does_not_block_read_only_routes(
        self, store: VectorStore, embed_model: StubEmbedding, llm: StubLLM
    ) -> None:
        app = self._app_with_api_key(store, embed_model, llm, api_key="secret-key")
        response = TestClient(app).get("/documents")
        assert response.status_code == 200

    def test_the_removed_provider_swap_endpoints_are_gone(
        self, client: TestClient
    ) -> None:
        assert client.post("/settings/llm", json={}).status_code == 404
        assert client.post("/settings/embedding", json={}).status_code == 404


class TestHealthEndpoint:
    def test_reports_ok_when_dependencies_are_fine(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert "checks" in body
        assert isinstance(body["ok"], bool)
        names = {c["name"] for c in body["checks"]}
        assert "poppler" in names
        assert "chroma_path_writable" in names


class TestExtractDocument:
    """`POST /extract` / `GET /extract/{document_id}` (ADR-25). `llm_calls` is
    monkeypatched at its module boundary — the same convention `test_extraction_engine.py`
    uses — so this exercises the real route -> engine -> schema translation path with
    only the outermost LLM call stubbed."""

    def test_successful_extraction_returns_page_cited_items(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            llm_calls,
            "extract_section_items",
            lambda llm, **kw: SectionExtractionResult(
                items=[
                    ExtractedItemLLM(
                        category="deadline",
                        text="Delivery within 30 days",
                        quote="Kubernetes",
                    )
                ]
            ),
        )

        response = client.post(
            "/extract",
            files={"file": ("report.pdf", build_pdf([ENGLISH_TEXT]), "application/pdf")},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "succeeded"
        assert body["filename"] == "report.pdf"
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["category"] == "deadline"
        assert item["located"] is True
        assert item["page_start"] == 1
        assert item["verification_status"] == "not_run"

    def test_no_extractable_text_is_a_failed_response_not_an_error(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/extract", files={"file": ("empty.txt", b"   ", "text/plain")}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "failed"
        assert body["items"] == []

    def test_unsupported_extension_is_a_400(self, client: TestClient) -> None:
        response = client.post(
            "/extract", files={"file": ("report.rtf", b"data", "application/rtf")}
        )
        assert response.status_code == 400

    def test_a_result_can_be_retrieved_after_extraction(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            llm_calls,
            "extract_section_items",
            lambda llm, **kw: SectionExtractionResult(items=[]),
        )
        posted = client.post(
            "/extract", files={"file": ("report.pdf", build_pdf([ENGLISH_TEXT]), "application/pdf")}
        )
        document_id = posted.json()["document_id"]

        response = client.get(f"/extract/{document_id}")

        assert response.status_code == 200
        assert response.json()["document_id"] == document_id

    def test_an_unknown_document_id_is_404(self, client: TestClient) -> None:
        response = client.get("/extract/does-not-exist")
        assert response.status_code == 404

    def test_extract_requires_the_api_key_when_configured(
        self, store: VectorStore, embed_model: StubEmbedding, llm: StubLLM
    ) -> None:
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes._get_settings] = (
            lambda: _settings(api_key="secret-key")
        )
        app.dependency_overrides[routes._get_vector_store] = lambda: store
        app.dependency_overrides[routes._get_embed_model] = lambda: embed_model
        app.dependency_overrides[routes._get_llm] = lambda: llm
        app.dependency_overrides[routes._get_job_store] = lambda: JobStore()
        app.dependency_overrides[routes._get_extraction_store] = lambda: ExtractionStore()

        response = TestClient(app).post(
            "/extract", files={"file": ("report.txt", b"some text", "text/plain")}
        )

        assert response.status_code == 401
