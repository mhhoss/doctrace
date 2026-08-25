# Embedding Model Selection for Persian/English Retrieval-Augmented Generation

*A measured comparison of BAAI/bge-m3 (fp32 and UINT8-quantized), intfloat/multilingual-e5-small, and
intfloat/multilingual-e5-base on retrieval quality, cross-lingual accuracy, score separability, and CPU
inference cost.*

**Mahdi Hosseini** — Independent technical benchmark, August 2026 (updated 2026-08-25)

---

## Abstract

This article reports a controlled, offline, CPU-only benchmark of embedding models for use as the
retrieval component of a Persian/English Retrieval-Augmented Generation (RAG) system: three open
multilingual text-embedding models — `BAAI/bge-m3`, `intfloat/multilingual-e5-small`, and
`intfloat/multilingual-e5-base` — and, in a follow-up run, a UINT8-quantized ONNX export of `bge-m3`
(`Xenova/bge-m3`) intended for fully offline, dependency-free deployment. Using a fixed, versioned
six-document corpus (English, Persian, and mixed-language documents) and a 23-query evaluation set
spanning same-language, cross-lingual, and out-of-corpus queries, we measure top-1 retrieval accuracy,
recall@5, score separability between answerable and unanswerable queries, and CPU embedding/query
latency. `bge-m3` (both precisions) achieved perfect top-1 accuracy and recall@5 (19/19 answerable
queries) and was the only model family with a positive separability gap, at a CPU cost 4–11× higher
than the two e5 models. The UINT8 export reproduces this quality at ~24% lower ingestion latency and
~37% lower query latency, at the cost of roughly halving the separability margin. We discuss the
resulting quality/latency/margin trade-off and recommend `bge-m3` for production use in this deployment
context, with the UINT8 export as the default for a fully local, network-independent execution mode.

## 1. Introduction

Retrieval-Augmented Generation (RAG) systems answer questions by first retrieving relevant passages
from a private document store and then conditioning a language model on that retrieved context. The
quality of the retrieval step is bounded by the embedding model used to represent both documents and
queries as vectors: a weak embedding model degrades every downstream answer regardless of how capable
the generation model is.

This problem is sharper in bilingual deployments — here, Persian and English — where a query and its
answer may not share a single surface token, and where a system that gates generation behind a fixed
retrieval-score threshold (to avoid answering from irrelevant context) additionally depends on the
embedding model producing a clean, consistent separation between "the answer is in the corpus" and "it
is not."

This article presents two benchmark runs against the same corpus, queries, and evaluation code, so that
any observed difference reflects the models rather than experimental variance. The first run (2026-08-19)
compares three candidate embedding models for a local-first, CPU-deployable knowledge assistant. The
second run (2026-08-25) adds a UINT8-quantized ONNX export of the winning model, evaluating whether
quantization — needed for a fully offline execution mode with no external service dependency — costs
anything in retrieval quality.

## 2. Methodology

### 2.1 Models under test

| Model | Publisher | Embedding dimension | Pooling strategy used |
| --- | --- | --- | --- |
| `BAAI/bge-m3` | BAAI | 1024 | CLS-token pooling |
| `Xenova/bge-m3` (UINT8 ONNX export) | Xenova | 1024 | CLS-token pooling |
| `intfloat/multilingual-e5-small` | intfloat | 384 | Mean pooling, `query:` / `passage:` prefixing |
| `intfloat/multilingual-e5-base` | intfloat | 768 | Mean pooling, `query:` / `passage:` prefixing |

Pooling and prefixing conventions follow each model's published usage recipe `[EXTERNAL]`; embedding
dimensions were read from each model's local configuration file `[MEASURED]`. All output vectors were
L2-normalized prior to scoring, and all similarity scores reported below are cosine similarity. The
UINT8 export is a dynamically-quantized ONNX artifact of the same underlying `BAAI/bge-m3` weights,
published by Xenova; it is pinned in this project at `models/xenova-bge-m3-uint8/`, with provenance and
file checksums recorded in that directory's `manifest.json`.

### 2.2 Corpus and query set

The evaluation corpus consists of six documents spanning three language conditions: English-only,
Persian-only, and mixed-language (a Persian-dominant document containing inline English technical
terms, and an English-dominant document containing embedded Persian script). Documents were sourced as
PDF and DOCX files to also exercise the system's real text-extraction path, including a documented
Persian-script PDF extraction artifact (word/character spacing degradation from `pdftotext`) that was
deliberately left uncorrected so the benchmark measures embedding behavior against the same degraded
input a production deployment would actually encounter.

23 queries were authored against this corpus and manually verified against the source text: 10
same-language queries (query and target document share a dominant language), 9 cross-lingual queries
(query language differs from the target document's dominant language, with no shared surface tokens),
and 4 out-of-corpus queries on topics manually confirmed absent from all six documents, used to measure
false-positive retrieval behavior.

### 2.3 Pipeline and evaluation procedure

Each document was parsed and chunked using the system's own production text-extraction and chunking
logic (unmodified), producing 45 chunks in total across the six documents, at a chunk size of 1024
characters with 128 characters of overlap. Each model embedded the identical 45 chunks and 23 queries.
Retrieval was computed as cosine similarity between each query vector and every chunk vector, ranked to
a top-5 result set per query.

For each answerable query (i.e. queries with a known expected source document), we recorded whether the
top-1 result was chunked from the correct document (*top-1 accuracy*) and whether any of the top-5
results came from the correct document (*recall@5*). For out-of-corpus queries, we recorded the top-1
similarity score as a measure of false-positive risk. *Separability gap* is defined as the minimum
top-1 score among answerable queries minus the maximum top-1 score among out-of-corpus queries: a
positive value means a single fixed similarity threshold exists that accepts every genuine answer while
rejecting every out-of-corpus query; a negative value means no such threshold exists, and any fixed
cutoff must trade off false accepts against false rejects.

### 2.4 Benchmark environment and hardware

| Parameter | Value |
| --- | --- |
| CPU | Intel Core i5-6200U (2 physical cores / 4 threads), CPU-only inference |
| Inference runtime | ONNX Runtime, `CPUExecutionProvider`, 4 intra-op threads |
| Network | Fully offline — no model downloads or network access during either run |
| Execution order | Sequential: one model loaded, benchmarked, and released before the next started |
| Chunking | 1024-character chunks, 128-character overlap (production defaults) |
| Batch size | 8 texts per embedding batch |

Models were embedded through ONNX Runtime rather than a PyTorch/sentence-transformers runtime,
reflecting the lighter dependency surface of a bundled, CPU-only deployment. Latency figures below are
representative of this runtime and this hardware, not of GPU-served or vendor-hosted inference of the
same models. Absolute latency varies run-to-run with machine load; comparisons across the two runs
below are made against each run's own paired `bge-m3` (fp32) baseline, never across runs.

## 3. Results — Run 1 (2026-08-19): fp32 model selection

### 3.1 Retrieval quality

| Model | Top-1 accuracy | Recall@5 | Same-language acc. | Cross-lingual acc. |
| --- | --- | --- | --- | --- |
| **bge-m3** | **100.0% (19/19)** | **100.0%** | **100.0%** | **100.0%** |
| multilingual-e5-small | 94.7% (18/19) | 94.7% | 100.0% | 88.9% |
| multilingual-e5-base | 89.5% (17/19) | 100.0% | 100.0% | 77.8% |

### 3.2 Cross-lingual retrieval failures

All three models retrieved correctly on every same-language query. Errors were confined to the
cross-lingual category. Both e5 models missed query `q11` (a Persian query targeting an English-only
document on compound interest); e5-base additionally missed `q17` (an English query targeting a
Persian-dominant document, which itself contains the English phrase "artificial intelligence" inline —
noted in the query set as a partially-shared-surface-term case). bge-m3 made no errors in either query
set.

### 3.3 Score separability

| Model | Min top-1 score, answerable | Max top-1 score, out-of-corpus | Separability gap |
| --- | --- | --- | --- |
| bge-m3 | 0.487 | 0.462 | **+0.024** |
| multilingual-e5-small | 0.779 | 0.826 | −0.047 |
| multilingual-e5-base | 0.737 | 0.786 | −0.049 |

bge-m3's out-of-corpus score band sits entirely below its answerable-query score range; for both e5
models the out-of-corpus band overlaps the bottom of the answerable range — the signature of a negative
separability gap.

### 3.4 CPU performance

| Model | Ingestion (ms/chunk) | Ingestion throughput | Query latency (ms) |
| --- | --- | --- | --- |
| bge-m3 | 2604.9 | 0.38 chunks/s | 118.1 |
| multilingual-e5-small | **241.4** | **4.14 chunks/s** | **12.1** |
| multilingual-e5-base | 642.8 | 1.56 chunks/s | 40.2 |

## 4. Quality / Speed Trade-off Analysis (Run 1)

The three models separate cleanly along two axes that move in opposite directions. bge-m3 dominates on
every quality metric measured — perfect top-1 accuracy, perfect recall@5, perfect cross-lingual
accuracy, and the only positive separability gap — while being 4.1× to 10.8× slower to embed per chunk
than e5-base and e5-small respectively, and 2.9× to 9.8× slower per query. e5-small is the fastest model
tested by a wide margin but has the weakest cross-lingual accuracy and the largest-magnitude negative
separability gap.

The separability gap is the more consequential of the two axes for a system that gates generation
behind a fixed similarity threshold to prevent answering from irrelevant context. A positive gap
(bge-m3) means one threshold value exists that accepts every genuine answer and rejects every
out-of-corpus query in this evaluation set. A negative gap (both e5 models) means the answerable and
out-of-corpus score distributions overlap: no fixed threshold can simultaneously avoid rejecting a valid
answer and accepting an ungrounded one. On this evidence, adopting either e5 model without re-deriving
and accepting a weaker threshold would trade a demonstrated groundedness property for an unverified one.

CPU latency, in contrast, is a deployment-tier cost rather than a correctness property: it can be
mitigated by GPU-served or hosted inference of the same model without changing which model is used or
re-validating retrieval quality — or, as Run 2 below shows, by quantization.

## 5. Results — Run 2 (2026-08-25): does UINT8 quantization cost retrieval quality?

`bge-m3` was selected as the production embedding model on the basis of Run 1 (Section 4). A UINT8
dynamically-quantized ONNX export of the same model, published by Xenova, was benchmarked against the
identical corpus and query set to evaluate it as the model backing a fully offline execution mode — one
with no HTTP call and no external network dependency at all. `bge-m3` (fp32) was re-run in the same
session as a paired baseline, since absolute latency is not comparable across separate benchmark runs
(Section 2.4).

| Model | Top-1 accuracy | Recall@5 | Same-lang. acc. | Cross-lingual acc. | Separability gap |
| --- | --- | --- | --- | --- | --- |
| bge-m3 (fp32, paired baseline) | 100.0% | 100.0% | 100.0% | 100.0% | +0.024 |
| **bge-m3-uint8 (Xenova)** | **100.0%** | **100.0%** | **100.0%** | **100.0%** | **+0.011** |

| Model | Ingestion (ms/chunk) | Ingestion throughput | Query latency (ms) |
| --- | --- | --- | --- |
| bge-m3 (fp32, paired baseline) | 3091.6 | 0.32 chunks/s | 170.4 |
| **bge-m3-uint8 (Xenova)** | **2345.9** | **0.43 chunks/s** | **108.1** |

**Retrieval quality is unchanged**: identical top-1 accuracy and recall@5 to fp32, including every
cross-lingual query, on this corpus. **The separability gap roughly halves** (0.024 → 0.011 in this
paired run): still positive — a fixed threshold can still separate answerable from out-of-corpus queries
on this evaluation set — but the margin between the lowest genuine answer and the highest false positive
is markedly thinner than fp32's. This is the measurable cost of quantization on this benchmark: not a
loss of top-1/recall accuracy, but a narrower safety margin around the groundedness threshold.
**Performance improves substantially**: ~24% faster ingestion and ~37% faster query, consistent with
INT8 arithmetic on CPU, with no separate embedding service to operate.

## 6. Production Recommendation

For a Persian/English RAG deployment that depends on a fixed retrieval-score threshold to enforce
groundedness, **BAAI/bge-m3** is the recommended embedding model family based on this benchmark. It is
the only model tested with a positive separability gap and the only one with perfect cross-lingual
retrieval accuracy on this evaluation set.

Within the bge-m3 family, the choice between fp32 and the UINT8 export is a deployment decision, not a
quality one: **the UINT8 export is recommended as the default for a fully local, network-independent
execution mode** — it reproduces fp32's measured accuracy and recall at meaningfully lower CPU cost,
which is exactly the profile a local-first, dependency-free deployment needs. Its thinner separability
margin means the retrieval-score threshold should be independently re-measured for that execution mode
rather than inherited from the fp32 measurement, especially before scaling past this benchmark's small
corpus. Where hosted or GPU-served fp32 inference is available and its higher per-chunk cost is
acceptable, fp32 remains the higher-margin choice.

Substituting either e5 model for bge-m3 (in either precision) would require accepting a measurably
weaker groundedness guarantee and materially worse cross-lingual retrieval. Where an application does
not require a fixed-threshold groundedness gate and can tolerate a small, measured reduction in
cross-lingual accuracy, multilingual-e5-small offers markedly lower CPU latency and remains a defensible
choice.

## 7. Limitations and Threats to Validity

- **Corpus size.** Six documents and 23 queries (19 answerable, 4 out-of-corpus) is a small evaluation
  set. Reported accuracies and separability gaps may shift, in either direction, at larger scale — the
  UINT8 export's halved separability margin in particular has not been re-validated on a larger corpus.
- **Topic coverage.** Each topic in the corpus is represented by exactly one document, so the benchmark
  does not test ranking among multiple same-topic documents of varying relevance.
- **Language-mixing patterns.** Only two mixed-language patterns are represented (Persian-dominant with
  inline English terms, and English-dominant with embedded Persian script). Real-world documents may mix
  languages differently, e.g. at the sentence level.
- **Document complexity.** No scanned/image-based PDFs, tables, or complex page layouts are represented
  in the corpus.
- **Known extraction artifact.** Text extracted from the two Persian-script PDF documents shows a
  previously documented spacing degradation from the PDF-to-text tool used; this was left uncorrected so
  results reflect production extraction behavior, but it may understate the achievable retrieval quality
  on cleanly extracted Persian PDF text.
- **Hardware and runtime specificity.** CPU latency figures are specific to the tested processor and to
  ONNX Runtime CPU inference; they do not generalize to GPU or vendor-hosted serving of the same models.
- **Cross-run latency comparisons.** Absolute latency figures vary between the two benchmark runs with
  machine load (Section 2.4); Section 5's comparison is only valid within its own paired run, and is not
  compared to Section 3's Run 1 figures in absolute terms.
- **Single run per model per session.** Each model was embedded once per corpus/query set within a
  given session, without repeated trials to characterize latency variance.

## 8. References

1. BAAI, *bge-m3: Multi-Lingual, Multi-Functionality, Multi-Granularity Text Embeddings*, model card and
   documentation. `[EXTERNAL, MODEL PROVENANCE ONLY]`
2. Wang et al. (intfloat), *Multilingual E5 Text Embeddings*, model cards for `multilingual-e5-small`
   and `multilingual-e5-base`. `[EXTERNAL, MODEL PROVENANCE ONLY]`
3. Xenova, *bge-m3* (UINT8 ONNX export), model repository. `[EXTERNAL, MODEL PROVENANCE ONLY]` — pinned
   copy and checksums at `models/xenova-bge-m3-uint8/manifest.json` in this project.
4. ONNX Runtime, *CPU Execution Provider* documentation. `[EXTERNAL, RUNTIME PROVENANCE ONLY]`

All quantitative results reported in Sections 3–5 of this article are original measurements produced by
the benchmark described in Section 2 (`eval/run_benchmark.py`) and are labeled `[MEASURED]` throughout;
statements drawn from model documentation rather than this benchmark are labeled `[EXTERNAL]`. Raw
per-model output for Section 3–4 (Run 1, 2026-08-19) is versioned at `eval/results/*.json` — frozen,
never overwritten by a later run. Raw output for Section 5 (Run 2, 2026-08-25) is at
`eval/results/2026-08-25/*.json`.

---

*Independent technical article. All measurements were produced by offline, CPU-only benchmark runs
described in Section 2; no external benchmark results, published leaderboard scores, or unverified
claims about model behavior are presented as measured data in this article.*
