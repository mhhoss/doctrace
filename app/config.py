"""Typed settings and provider client construction.

The single source of credentials, model names, and provider URLs (invariant 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:  # heavy imports stay out of module import time
    from llama_index.core.base.embeddings.base import BaseEmbedding
    from llama_index.core.llms import LLM


class Settings(BaseSettings):
    """Application configuration, resolved from the environment and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Chat/completion provider (OpenAI-compatible).
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # Embedding provider. Left unset, it reuses the LLM credentials and endpoint;
    # see ADR-5 for why these are separable at all. The shipped default pairs with the
    # README's Quick start: BAAI/bge-m3 served locally via Ollama — a real base URL
    # (e.g. EMBEDDING_BASE_URL=http://127.0.0.1:11434/v1) still needs setting in `.env`
    # for that pairing to actually run; a hosted embedding provider remains fully
    # supported by setting these three variables to it instead (ADR-5, R-08).
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None
    embedding_model: str = "bge-m3"
    # "onnx_local" (ADR-23) skips HTTP entirely and loads a pinned ONNX model from
    # disk — see `embedding_onnx_model_dir` below. Left as the default for every
    # existing deployment; nothing changes unless this is explicitly set.
    embedding_provider: Literal["openai_compatible", "onnx_local"] = (
        "openai_compatible"
    )
    embedding_batch_size: int = Field(default=5, ge=1)
    embedding_timeout_seconds: float = Field(default=300.0, gt=0.0)
    embedding_onnx_model_dir: Path = Path("models/xenova-bge-m3-uint8")
    embedding_onnx_intra_threads: int = Field(default=4, ge=1)

    chroma_path: Path = Path("./chroma_db")
    # Chroma's own naming rule, enforced here so a bad value fails at startup.
    chroma_collection: str = Field(
        default="knowledge_base",
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,510}[a-zA-Z0-9]$",
    )
    data_dir: Path = Path("./data")

    # Character counts, not tokens (ADR-9).
    chunk_size: int = Field(default=1024, ge=128)
    chunk_overlap: int = Field(default=128, ge=0)

    # Narrowed from 5 to 3 for groundedness (ADR-20); not independently re-swept,
    # and `retrieval_min_score` below was measured at top_k=5.
    retrieval_top_k: int = Field(default=3, ge=1)
    # Measured against BAAI/bge-m3 on the eval/ corpus (23 queries, 6 docs); see
    # ARCHITECTURE.md open question 2.
    retrieval_min_score: float = Field(default=0.47, ge=0.0, le=1.0)

    api_base_url: str = "http://127.0.0.1:8000"

    # Standard Python logging level names (DEBUG/INFO/WARNING/ERROR/CRITICAL).
    log_level: str = "INFO"

    # Unset (default) disables auth entirely — appropriate only for a single-user
    # deployment kept off any network the operator doesn't fully trust (ADR-26). Set
    # to require a matching `X-API-Key` header on every mutating request.
    api_key: str | None = None
    # Enforced in the ingestion route before a file is read into memory (ADR-26).
    max_upload_mb: int = Field(default=50, ge=1)

    @model_validator(mode="after")
    def _resolve(self) -> Settings:
        if self.embedding_api_key is None:
            self.embedding_api_key = self.llm_api_key
        if self.embedding_base_url is None:
            self.embedding_base_url = self.llm_base_url
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        return self

    @property
    def embedding_fingerprint(self) -> str:
        """Identity of the embedding configuration, per ADR-8.

        The base URL is intentionally excluded: the same model behind a different
        gateway is still the same model.

        "openai_compatible" (the only provider that existed originally) keeps the
        exact pre-existing format — bare `embedding_model` — so upgrading never
        invalidates a fingerprint already persisted in an existing `chroma_db/`
        (ADR-8 would otherwise misfire as a false mismatch on the very next startup).
        "onnx_local" (ADR-23) gets a distinguishing prefix plus its fixed output
        dimension (1024, per the pinned artifact's manifest) folded in, so it can
        never collide with an `openai_compatible` collection using the same model
        family name (e.g. both being called "bge-m3").
        """
        if self.embedding_provider == "onnx_local":
            return f"onnx_local:{self.embedding_model}:1024"
        return self.embedding_model


@lru_cache
def get_settings() -> Settings:
    return Settings()


def require_credentials(settings: Settings) -> None:
    """Fail loudly if a provider credential is blank, rather than at the first query.

    `Settings` itself allows a blank `llm_api_key`/`embedding_api_key` (the default),
    since tests construct `Settings` directly with stub providers that never need one.
    Real application startup calls this separately so a forgotten `.env` fails at
    startup with an actionable message instead of an opaque 502 on first use.
    """
    # "onnx_local" needs no credential at all — it never makes a network call.
    required: list[tuple[str, str | None]] = [("LLM_API_KEY", settings.llm_api_key)]
    if settings.embedding_provider != "onnx_local":
        required.append(("EMBEDDING_API_KEY", settings.embedding_api_key))
    missing = [name for name, value in required if not value]
    if missing:
        raise RuntimeError(
            f"Missing required configuration: {', '.join(missing)}. Copy "
            ".env.example to .env and fill in your provider credentials before "
            "starting the app."
        )


@dataclass(frozen=True)
class ProviderDescription:
    """What a user may safely be shown about one configured provider.

    Deliberately carries a *masked* key, never the secret: this is the only
    representation of provider configuration that leaves the process, so the masking
    cannot be forgotten at a call site. `base_url` carries no credential and is shown
    in full (not just `host`) so a runtime-edit form can be pre-filled with it.
    """

    model: str
    host: str
    base_url: str
    masked_key: str
    is_local: bool
    provider: Literal["openai_compatible", "onnx_local"] = "openai_compatible"
    dimension: int | None = None


def mask_secret(value: str) -> str:
    """Render a credential as recognizable-but-unusable, e.g. `sk-or-v1••••••4f2a`."""
    if not value:
        return "not set"
    if len(value) <= 8:
        return "•" * 8
    return f"{value[:8]}{'•' * 6}{value[-4:]}"


def _is_local_host(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"} or host.endswith(
        ".local"
    )


def describe_providers(settings: Settings) -> tuple[ProviderDescription, ProviderDescription]:
    """Describe the LLM and embedding providers for display: `(llm, embedding)`."""
    return (
        ProviderDescription(
            model=settings.llm_model,
            host=urlparse(settings.llm_base_url).netloc or settings.llm_base_url,
            base_url=settings.llm_base_url,
            masked_key=mask_secret(settings.llm_api_key),
            is_local=_is_local_host(settings.llm_base_url),
        ),
        _describe_embedding(settings),
    )


def _describe_embedding(settings: Settings) -> ProviderDescription:
    if settings.embedding_provider == "onnx_local":
        # No network endpoint at all: reports the pinned model directory in place of
        # a base URL/key, both of which are meaningless for this provider.
        return ProviderDescription(
            model=settings.embedding_model,
            host="local",
            base_url=str(settings.embedding_onnx_model_dir),
            masked_key="not applicable (local ONNX model)",
            is_local=True,
            provider="onnx_local",
            dimension=1024,
        )
    embedding_base_url = settings.embedding_base_url or ""
    return ProviderDescription(
        model=settings.embedding_model,
        host=urlparse(embedding_base_url).netloc or embedding_base_url,
        base_url=embedding_base_url,
        masked_key=mask_secret(settings.embedding_api_key or ""),
        is_local=_is_local_host(embedding_base_url),
        provider="openai_compatible",
        dimension=None,
    )


def probe_llm(llm: LLM) -> None:
    """Make the smallest real chat call there is. Raises if the provider is unusable."""
    from llama_index.core.base.llms.types import ChatMessage, MessageRole

    llm.chat([ChatMessage(role=MessageRole.USER, content="ping")])


def probe_embedding(embed_model: BaseEmbedding) -> None:
    """Embed one short string for real. Raises if the provider is unusable."""
    embed_model.get_query_embedding("ping")


def build_embedding_model(settings: Settings) -> BaseEmbedding:
    """Construct the embedding client. The only place embedding credentials are used.

    `model_name`, not `model`: `OpenAIEmbedding`'s `model` argument is validated against
    a fixed enum of legacy OpenAI model names and rejects anything else (including
    current OpenAI models the pinned llama-index version predates, and any non-OpenAI
    model served through an OpenAI-compatible gateway). `model_name` bypasses that
    lookup and is sent to the API as-is, which is what R-08's "any OpenAI-compatible
    provider, no code change" actually requires.
    """
    if settings.embedding_provider == "onnx_local":
        return _build_onnx_embedding(settings)

    from llama_index.embeddings.openai import OpenAIEmbedding

    return OpenAIEmbedding(
        model_name=settings.embedding_model,
        api_key=settings.embedding_api_key,
        api_base=settings.embedding_base_url,
        embed_batch_size=settings.embedding_batch_size,
        timeout=settings.embedding_timeout_seconds,
    )


def _build_onnx_embedding(settings: Settings) -> BaseEmbedding:
    """Fully local, offline embedding client for the pinned ONNX model (ADR-23).

    Pooling/normalization match `eval/run_benchmark.py`'s `OnnxEmbedder`: CLS-token
    pooling, L2 normalization, 512-token truncation, no query/passage prefix.
    """
    from typing import Any

    import onnxruntime as ort
    from llama_index.core.base.embeddings.base import BaseEmbedding as _BaseEmbedding
    from pydantic import PrivateAttr
    from tokenizers import Tokenizer

    model_dir = settings.embedding_onnx_model_dir
    onnx_path = model_dir / "model_uint8.onnx"
    tokenizer_path = model_dir / "tokenizer.json"
    for path in (onnx_path, tokenizer_path):
        if not path.is_file():
            raise RuntimeError(
                f"onnx_local embedding provider: missing pinned model file {path}. "
                f"See {model_dir / 'manifest.json'} for how this artifact is pinned."
            )

    class _OnnxLocalEmbedding(_BaseEmbedding):
        onnx_path: Path
        intra_threads: int
        _tokenizer: Any = PrivateAttr(default=None)
        _session: Any = PrivateAttr(default=None)
        _input_names: set = PrivateAttr(default_factory=set)

        @classmethod
        def class_name(cls) -> str:
            return "onnx_local_embedding"

        def model_post_init(self, context: object, /) -> None:
            self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
            self._tokenizer.enable_truncation(max_length=512)
            pad_id = self._tokenizer.token_to_id("<pad>")
            pad_token = "<pad>"
            if pad_id is None:
                pad_id = self._tokenizer.token_to_id("[PAD]")
                pad_token = "[PAD]"
            self._tokenizer.enable_padding(pad_id=pad_id or 0, pad_token=pad_token)
            self._session = None  # built lazily by `_ensure_session`

        def _ensure_session(self) -> None:
            if self._session is not None:
                return
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            so.intra_op_num_threads = self.intra_threads
            so.inter_op_num_threads = 1
            self._session = ort.InferenceSession(
                str(self.onnx_path), sess_options=so, providers=["CPUExecutionProvider"]
            )
            self._input_names = {i.name for i in self._session.get_inputs()}

        def set_intra_threads(self, intra_threads: int) -> None:
            """Rebuild the session with a new thread count (used by the ingestion
            script's thermal governor to reduce CPU pressure mid-run)."""
            if intra_threads == self.intra_threads and self._session is not None:
                return
            self.intra_threads = intra_threads
            self._session = None
            self._ensure_session()

        def _embed(self, texts: list[str]) -> list[list[float]]:
            import numpy as np

            if not texts:
                return []
            self._ensure_session()
            enc = self._tokenizer.encode_batch(texts)
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            feeds = {k: v for k, v in feeds.items() if k in self._input_names}
            out = self._session.run(None, feeds)[0]
            vec = out[:, 0, :]  # CLS-token pooling (bge-m3's published dense recipe)
            vec = vec / np.clip(np.linalg.norm(vec, axis=1, keepdims=True), 1e-12, None)
            return vec.astype(np.float32).tolist()

        def _get_query_embedding(self, query: str) -> list[float]:
            return self._embed([query])[0]

        def _get_text_embedding(self, text: str) -> list[float]:
            return self._embed([text])[0]

        def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
            return self._embed(texts)

        async def _aget_query_embedding(self, query: str) -> list[float]:
            return self._get_query_embedding(query)

    return _OnnxLocalEmbedding(
        model_name=settings.embedding_model,
        onnx_path=onnx_path,
        intra_threads=settings.embedding_onnx_intra_threads,
        embed_batch_size=settings.embedding_batch_size,
    )


def build_llm(settings: Settings) -> LLM:
    """Construct the chat/completion client. The only place LLM credentials are used.

    Works around the same kind of catalog restriction `build_embedding_model` already
    documents, but on the chat side: `OpenAI.metadata` (read on every `chat()` call, via
    `to_payload()`) computes `context_window` by looking `model` up in a fixed table of
    official OpenAI model names and raises `ValueError` for anything else. Unlike the
    embedding client, there is no `model_name=`-style escape hatch here, and the lookup
    happens lazily at call time, not construction time — so it does not surface until a
    real chat request is made with a non-catalog model name. Verified against a real
    OpenRouter call: `openai/gpt-4o-mini` (and every other gateway-prefixed or
    non-OpenAI model id) raises there. Without this override, R-08 ("any
    OpenAI-compatible provider, no code change") would be false for every provider
    except literal, unprefixed OpenAI model names.
    """
    from llama_index.core.base.llms.types import LLMMetadata
    from llama_index.llms.openai import OpenAI
    from llama_index.llms.openai.utils import openai_modelname_to_contextsize

    class _GatewayCompatibleOpenAI(OpenAI):
        @property
        def metadata(self) -> LLMMetadata:
            try:
                context_window = openai_modelname_to_contextsize(self._get_model_name())
            except ValueError:
                # Not one of OpenAI's own catalog names (any OpenRouter/gateway
                # model id lands here). 128k is a generous, non-authoritative
                # estimate; an actual mismatch surfaces as a provider error on the
                # request itself, not a silent truncation.
                context_window = 128_000
            return LLMMetadata(
                context_window=context_window,
                num_output=self.max_tokens or -1,
                is_chat_model=True,
                is_function_calling_model=True,
                model_name=self.model,
            )

    return _GatewayCompatibleOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        api_base=settings.llm_base_url,
        # Same question + same context -> same answer (ADR-20): sampling variance
        # would make the refusal sentinel and the eval corpus irreproducible.
        temperature=0.0,
    )


@dataclass
class ProviderRegistry:
    """The LLM and embedding clients currently in effect (ADR-10 amendment).

    The only mutable provider state in the process. This holds the result of
    `build_llm`/`build_embedding_model`, it does not construct anything itself —
    `config.py` remains the sole place credentials are read and clients are built
    (invariant 5). A caller must build and probe a replacement *before* calling
    `replace_llm`/`replace_embedding`, so a client is only ever swapped in once it has
    already proven reachable; a request already holding the previous client via
    dependency injection simply finishes with it (no in-place mutation of the objects
    themselves, only of which instance this registry hands out next).
    """

    settings: Settings
    llm: LLM
    embed_model: BaseEmbedding

    def replace_llm(self, *, settings: Settings, llm: LLM) -> None:
        self.settings = settings
        self.llm = llm

    def replace_embedding(self, *, settings: Settings, embed_model: BaseEmbedding) -> None:
        self.settings = settings
        self.embed_model = embed_model
