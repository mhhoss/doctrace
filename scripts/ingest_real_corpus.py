"""One-off operational script: bulk-ingest `real_corpus/txt/` through the onnx_local
embedding provider (ADR-23), with a CPU-thermal governor.

Not part of the shipped application (same spirit as `eval/run_benchmark.py`) — it only
calls into `app.rag.engine`/`app.storage.vector_store` as a normal caller would. Safe to
re-run: `index_document`'s content-hash dedup (ADR-3/ADR-21) makes every file idempotent,
so an interrupted run just re-checks already-indexed files and continues with the rest.

Usage: uv run python scripts/ingest_real_corpus.py
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

from app.config import Settings, build_embedding_model
from app.rag.engine import ingest_file
from app.storage.vector_store import VectorStore

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = REPO_ROOT / "real_corpus" / "txt"

# Dedicated collection: ADR-8's fingerprint check already refuses to mix embedding
# models into one collection, but naming it explicitly makes the separation legible
# rather than accidental.
ONNX_COLLECTION = "knowledge_base_onnx_bge_m3"

TEMP_HIGH_C = 85.0
TEMP_SAFE_C = 78.0  # resume threshold: a gap below TEMP_HIGH_C avoids flapping
MIN_THREADS = 1
CONSECUTIVE_HIGH_TO_THROTTLE = 3
POLL_INTERVAL_SECONDS = 2.0


def read_cpu_temp_c() -> float | None:
    """Best-effort CPU package temperature via `sensors -j` (coretemp), Linux only.

    Returns None if unavailable rather than raising — a missing sensor must not abort
    a real ingestion run; it only means the thermal governor cannot act.
    """
    try:
        out = subprocess.run(
            ["sensors", "-j"], capture_output=True, text=True, timeout=5, check=True
        ).stdout
        data = json.loads(out)
    except Exception:  # noqa: BLE001 - a missing/broken sensor must not abort ingestion
        return None
    for chip, readings in data.items():
        if not chip.startswith("coretemp"):
            continue
        for _label, fields in readings.items():
            if not isinstance(fields, dict):
                continue
            for key, value in fields.items():
                if key.endswith("_input") and "package" in _label.lower():
                    return float(value)
    return None


class OnnxLocalEmbedding(Protocol):
    """The subset of the onnx_local embedding client this governor drives."""

    def set_intra_threads(self, intra_threads: int) -> None: ...


@dataclass
class ThermalGovernor:
    """Watches CPU temperature between files; reduces ONNX threads or pauses ingestion
    when sustained above TEMP_HIGH_C, then restores full threads once it cools down."""

    embed_model: OnnxLocalEmbedding
    normal_threads: int
    consecutive_high: int = 0
    throttled: bool = False
    log: list[str] = field(default_factory=list)

    def check(self) -> None:
        temp = read_cpu_temp_c()
        if temp is None:
            return
        if temp >= TEMP_HIGH_C:
            self.consecutive_high += 1
        else:
            self.consecutive_high = 0
        if self.consecutive_high >= CONSECUTIVE_HIGH_TO_THROTTLE and not self.throttled:
            self.throttled = True
            reduced = max(MIN_THREADS, self.normal_threads // 2)
            msg = f"CPU {temp:.1f}C sustained >= {TEMP_HIGH_C}C: reducing ONNX threads {self.normal_threads} -> {reduced}"
            print(f"[thermal] {msg}")
            self.log.append(msg)
            self.embed_model.set_intra_threads(reduced)
            self._wait_for_cooldown()
        elif self.throttled and temp <= TEMP_SAFE_C:
            msg = f"CPU {temp:.1f}C <= {TEMP_SAFE_C}C: restoring ONNX threads to {self.normal_threads}"
            print(f"[thermal] {msg}")
            self.log.append(msg)
            self.embed_model.set_intra_threads(self.normal_threads)
            self.throttled = False
            self.consecutive_high = 0

    def _wait_for_cooldown(self) -> None:
        while True:
            time.sleep(POLL_INTERVAL_SECONDS)
            temp = read_cpu_temp_c()
            if temp is None or temp <= TEMP_SAFE_C:
                if temp is not None:
                    msg = f"CPU cooled to {temp:.1f}C: resuming"
                    print(f"[thermal] {msg}")
                    self.log.append(msg)
                return
            msg = f"CPU still {temp:.1f}C: pausing ingestion"
            print(f"[thermal] {msg}")
            self.log.append(msg)


def main() -> None:
    files = sorted(CORPUS_DIR.glob("*.txt"))
    if not files:
        raise SystemExit(f"No .txt files found under {CORPUS_DIR}")

    settings = Settings(
        llm_api_key="unused-for-ingestion",
        llm_base_url="http://127.0.0.1:0",
        llm_model="unused-for-ingestion",
        embedding_provider="onnx_local",
        embedding_model="xenova-bge-m3-uint8",
        chroma_collection=ONNX_COLLECTION,
    )
    embed_model = build_embedding_model(settings)
    store = VectorStore(
        path=settings.chroma_path,
        collection_name=settings.chroma_collection,
        embedding_fingerprint=settings.embedding_fingerprint,
    )

    # `embedding_provider="onnx_local"` above guarantees this method exists at
    # runtime; `BaseEmbedding` just has no static type for it (ADR-23).
    governor = ThermalGovernor(
        embed_model=cast(OnnxLocalEmbedding, embed_model),
        normal_threads=settings.embedding_onnx_intra_threads,
    )

    results = []
    total_chunks = 0
    t_start = time.perf_counter()
    for path in files:
        governor.check()
        content = path.read_bytes()
        t0 = time.perf_counter()
        outcome = ingest_file(
            store=store,
            embed_model=embed_model,
            filename=path.name,
            content=content,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )
        dt = time.perf_counter() - t0
        total_chunks += outcome.chunk_count
        results.append((path.name, outcome, dt))
        print(f"{path.name}: {outcome.status.value} chunks={outcome.chunk_count} time={dt:.1f}s")
        if outcome.status == "failed":
            print(f"  error: {outcome.error}")

    elapsed = time.perf_counter() - t_start

    print("\n=== Summary ===")
    print(f"Files: {len(files)}")
    print(f"Total chunks indexed: {total_chunks}")
    print(f"Elapsed: {elapsed:.1f}s")
    if total_chunks:
        print(f"Throughput: {total_chunks / elapsed:.3f} chunks/s")
    print(f"Embedding fingerprint: {settings.embedding_fingerprint}")
    print(f"Chroma collection: {settings.chroma_collection}")
    failed = [r for r in results if r[1].status == "failed"]
    print(f"Failures: {len(failed)}")
    if governor.log:
        print("\n=== Thermal events ===")
        for line in governor.log:
            print(line)


if __name__ == "__main__":
    main()
