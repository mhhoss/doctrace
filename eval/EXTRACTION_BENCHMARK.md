# Extraction Pipeline Evaluation: Requirement Recall, Citation Accuracy, and Groundedness

*A measured evaluation of `app/extraction/` (ADR-25) against a small, hand-labeled corpus
of Persian, English, and mixed-language clause-structured documents — including a real
prompt defect found, fixed, and re-verified in the course of this evaluation.*

**Mahdi Hosseini** — August 2026

---

## Abstract

This article evaluates the document-intelligence extraction pipeline — sectionize →
per-section structured LLM extraction → deterministic page attachment → dedup →
independent grounding verification — against a 35-item hand-labeled corpus of four
synthetic clause-structured documents (English, Persian, and mixed-language, PDF and
DOCX). The first run measured 74.3% recall and 74.3% precision, but diagnosis (not
guesswork) traced nearly all of the recall loss to one specific, fixable cause: the
extraction prompt did not instruct the model to preserve the source document's
language, so Persian clauses were silently translated into English, making them
unmatchable against Persian ground truth. One prompt-line fix and a full re-run
measured **91.4% recall, 97.0% precision, 100% citation accuracy (20/20), and 100%
grounding-verification pass rate** on the corrected run. This document reports both
runs, not just the second — the first run and its diagnosis are part of the evidence,
not a discarded draft.

## 1. Introduction

The chat-style RAG assistant this project also ships answers a question with `top_k`
retrieved chunks — adequate for "what does this say about X," structurally incapable of
"list every requirement in this document," which is a coverage problem, not a
similarity-search problem (ADR-25). The extraction pipeline exists to answer that
second question: read every section of a document and produce a structured,
page-cited register of every discrete obligation, requirement, deadline, and
prohibition it states.

A pipeline that produces *a* register is not the same as one that produces a *correct*
register. This evaluation exists to measure the difference, using the same discipline
this project's embedding-model benchmark (`eval/EMBEDDING_MODEL_BENCHMARK.md`) already
established: a fixed, versioned, disclosed corpus; automatic metrics with every match
written out for a human to spot-check, not trusted blindly; and an explicit
`[MEASURED]` vs. reasoning distinction.

## 2. Methodology

### 2.1 Corpus

Four synthetic documents, `eval/extraction_corpus/`, disclosed in full in that
directory's `manifest.json`:

| Document | Format | Language | Structure | Ground-truth items |
| --- | --- | --- | --- | --- |
| `doc-en-pdf-service-agreement` | PDF, 3 pages | English | 8 numbered sections | 14 |
| `doc-fa-pdf-tender` | PDF, 3 pages | Persian | 9 numbered clauses (`ماده`) | 9 |
| `doc-mixed-docx-it-project` | DOCX | Persian-dominant, inline English terms | 6 unstructured paragraphs | 5 |
| `doc-en-docx-data-policy` | DOCX | English | 7 unstructured paragraphs | 7 |

**Provenance, stated plainly**: these are not real client documents — none were
available for this project. Text was authored for this evaluation, covering realistic
clause shapes (numbered contract sections, Persian tender articles, unstructured
policy paragraphs) and every extraction category (`obligation`, `requirement`,
`deadline`, `prohibition`, `definition`). The two PDF files are generated via this
project's own byte-level PDF test-fixture builder (`tests/pdf_fixtures.py`), not
scanned or exported from a real document tool; the two DOCX files are real `.docx`
files written with `python-docx`. `eval/extraction_corpus/generate_corpus.py`
reproduces all four from the source text in that same file.

**Ground truth** (`ground_truth.json`, 35 items total): for each document, every
discrete extractable statement was listed by hand — `{doc_id, category, text, page}` —
by reading the corpus text directly, the same way `eval/queries.json`'s queries were
authored for the embedding benchmark. `page` is `null` for both DOCX documents, which
have no real page concept (ADR-24) — citation accuracy is only evaluated where a page
number is meaningful.

### 2.2 Metrics and matching

Ground-truth items are matched against extracted items by text similarity
(`difflib.SequenceMatcher`, the same metric `app/extraction/matching.py` already uses
for near-duplicate collapse), greedily, one-to-one, at a 0.55 threshold — not exact
string equality, since a correct extraction paraphrases rather than quoting the whole
clause. **Every match, and every non-match, is written to the run's `report.json`** so
a human can spot-check the automatic matching rather than trust the threshold blindly
— the matching itself is exactly the kind of judgment call that can silently distort a
reported number, and this evaluation does not hide that risk.

- **Recall**: fraction of ground-truth items matched.
- **Precision**: fraction of extracted items that matched a real ground-truth item
  (an unmatched extracted item is either a hallucination or a genuine but unlabeled
  finding — both are reported, not assumed).
- **Category accuracy**: of matched items, does the extracted category equal the
  ground-truth category.
- **Citation accuracy**: of matched items with a real page number (PDF only), does the
  extracted `page_start`–`page_end` range actually contain it. A stricter, distinct
  metric from recall — an item can be correctly extracted and mis-cited.
- **Verification rate**: fraction of matched items the grounding-verification pass
  (ADR-25 §6) marked `verified` rather than `failed`.

**What this evaluation does not measure, disclosed here rather than left implicit**:
the grounding verifier's *own* accuracy — whether it would actually catch a genuinely
wrong claim — is not tested, because this corpus contains no deliberately incorrect
claims to feed it. A 100% verification rate below means "the verifier found no problem
with any correctly-extracted item," which is expected and correct; it says nothing
about the verifier's sensitivity. Building a perturbed-negative test set for the
verifier itself (flip a number, drop a conditional) is scoped out of this evaluation
for cost and time, and is recorded here as a known gap, not silently omitted.

### 2.3 Environment

Real LLM calls, not a stub: `openai/gpt-4o-mini` via OpenRouter — the same
`build_llm(settings)` client the deployed application itself uses, exercised through
`app/extraction/engine.py::extract_document` unmodified. `verify=True` (grounding
verification runs on every matched item). Each run writes to
`eval/results/extraction/<run-tag>/report.json`, never overwriting a prior run (the
same discipline `eval/run_benchmark.py --run-tag` already established).

## 3. Results — Run 1 (2026-08-25)

| Metric | Value |
| --- | --- |
| Recall | 74.3% (26/35) |
| Precision | 74.3% (26/35 extracted matched; 9 unmatched) |
| Category accuracy | 57.7% (15/26 matched items) |
| Citation accuracy | **100%** (14/14 page-applicable matches) |
| Verification rate | 100% (26/26 matched items) |

All four documents completed (`status=succeeded`), all sections extracted without
error. On its face, a 74% recall/precision result — but the *pattern* of misses was
the more important finding.

### 3.1 Diagnosis: recall loss was concentrated, not distributed

**All 9 of `doc-fa-pdf-tender`'s ground-truth items were unmatched** — a striking,
suspicious concentration, not a scattered set of genuine misses. Cross-referencing
against `unmatched_extracted` showed why: the model *did* extract all nine Persian
clauses, but wrote the `text` field in **English** — e.g. ground truth `"پیمانکار موظف
است کار را ظرف نود روز از تاریخ ابلاغ قرارداد تکمیل نماید."` against extracted `"The
contractor is obliged to complete the work within ninety days from the date of
notification..."`. `difflib`'s text-similarity matcher, comparing a Persian string
against an English one, correctly scores this as no match — the extraction was
substantively correct, but unmatchable by this evaluation's own methodology. (One
Persian item, a `definition`, matched despite this — the model left the defined term
`"مدت قرارداد"` untranslated, which happened to keep enough of the sentence intact to
clear the similarity threshold. This inconsistency was itself informative.)

The root cause: `app/extraction/llm_calls.py`'s extraction prompt never instructed the
model to preserve the source document's language — a real gap, not a hypothesis, since
the model's behavior was internally inconsistent (translating most Persian clauses,
leaving one alone) rather than a single deliberate choice.

## 4. The Fix

One line added to the extraction prompt:

> "Write the statement in the SAME language as the section text below — never
> translate it, even if you would naturally respond in a different language."

No other change — not to `sectionize.py`, `matching.py`, or the verification prompt.

## 5. Results — Run 2, post-fix (2026-08-25-v2)

| Metric | Run 1 | Run 2 | Change |
| --- | --- | --- | --- |
| Recall | 74.3% | **91.4%** (32/35) | +17.1 pts |
| Precision | 74.3% | **97.0%** (32/33) | +22.7 pts |
| Category accuracy | 57.7% | 50.0% (16/32) | −7.7 pts |
| Citation accuracy | 100% | **100%** (20/20) | unchanged |
| Verification rate | 100% | 100% (32/32) | unchanged |

The fix worked as diagnosed: recall and precision both jumped sharply, and inspecting
the new report confirms Persian clauses now stay in Persian. This is the "measure →
diagnose → fix → re-verify" loop this project's own ADRs already practice (e.g.
ADR-19's threshold recalibration) applied to the extraction pipeline for the first
time — reported here with both runs shown, not just the improved one, because the
diagnosis is as much the result as the fix.

### 5.1 What's left after the fix

**Three ground-truth items remain unmatched**, all traced to one cause: a single
section (`doc-en-pdf-service-agreement`'s "Section 6. Liability") failed extraction
outright — the model returned a category value (`"limitation"`) outside the fixed
five-category enum, `structured_predict`'s Pydantic validation correctly rejected it,
and `app/extraction/engine.py`'s per-section failure isolation (ADR-25, mirroring
ADR-7) marked that one section `failed` and continued with the rest of the document
unaffected — exactly the designed behavior, not a bug. The two liability clauses in
that section, plus one separate definition clause that fell just under the match
threshold on this run, account for all three misses. **This is real, positive evidence
that the failure-isolation design works under an actual model failure, not only in
unit tests with an injected exception** — the first time this project has observed it
happen with a real provider.

**Category accuracy dropped slightly (57.7% → 50.0%) and is the weakest number in this
evaluation** — but inspecting the 16 mismatches shows this is substantially a taxonomy
problem, not an extraction problem: the model labels "The Client shall pay all
undisputed invoices within 15 days of receipt" `obligation` where the ground truth
says `deadline`; both are defensible. `obligation` and `requirement` in particular
overlap enough in ordinary English ("shall"/"must" clauses) that a careful model and a
careful human labeler disagree systematically, not randomly — the fix here is likely
to merge or more sharply define these two categories, not to prompt-engineer around
model behavior that is, on inspection, reasonable.

**One unmatched extracted item** (precision's only miss): "The Contractor shall
provide software maintenance services" — a real sentence in the source document
(Section 1) that the ground truth did not list as a separate item, since it reads as
scope description rather than a discrete obligation. Arguably a labeling gap on the
ground-truth side, not an extraction error.

## 6. Analysis

**Citation accuracy is the headline result: 100% across both runs, 20/20 page-checked
matches.** This is the number that matters most for the product's actual claim — that
every extracted item can be verified against an exact source page — and it held
without exception across two full runs and a substantive prompt change. Recall and
precision are what improved with iteration; citation correctness was never the weak
point.

**Verification rate (100%, both runs)** confirms the grounding pass does not
false-flag correctly-extracted items, but — stated plainly again — says nothing about
whether it would catch an incorrect one. Treat this number as "the verifier does not
cause false alarms on good extractions," not as "the verifier works," until a
perturbed-negative test exists.

**The single largest lesson of this evaluation is methodological, not architectural**:
the biggest score movement (+17 recall points) came from a one-line prompt fix
diagnosed by reading *where* the failures concentrated, not from redesigning the
pipeline. A pattern of "every item in one document missed" was the signal; treating it
as 9 independent random misses would have missed the actual, fixable cause entirely.

## 7. Limitations

- **Corpus size.** 35 items across 4 documents is small; the reported percentages have
  wide uncertainty and may shift, in either direction, at a larger scale — the same
  caveat `eval/EMBEDDING_MODEL_BENCHMARK.md` already carries for its own corpus.
- **Synthetic corpus.** No real client document was available; text was authored for
  this evaluation specifically, disclosed in `manifest.json`. It was written to be
  representative of real clause-structured documents, not to be easy for the pipeline.
- **Single labeler, single model, single run per condition.** No inter-annotator
  agreement check; results are not repeated-trial averages, so latency and score
  variance from LLM sampling are not characterized (temperature is 0.0 project-wide,
  which bounds but does not eliminate this).
- **The grounding verifier's own accuracy is untested** (§2.2) — the most consequential
  gap in this evaluation, since a verifier that never catches a real error provides no
  actual safety margin, and this evaluation cannot currently distinguish that failure
  mode from a genuinely reliable verifier.
- **Match-threshold sensitivity.** The 0.55 similarity threshold was not independently
  tuned against this corpus; a different value could move recall/precision in either
  direction. Every match is logged specifically so this can be audited, not just
  asserted.
- **Category taxonomy.** §5.1's finding suggests `obligation`/`requirement` may not be
  a clean, human-reproducible distinction as currently defined — a taxonomy question
  worth resolving before category accuracy is treated as a reliable number at all.

## 8. Reproducing this evaluation

```
uv run python eval/extraction_corpus/generate_corpus.py   # regenerate the 4 corpus files
uv run python eval/run_extraction_eval.py                 # writes eval/results/extraction/<today>/report.json
```

Requires a working `LLM_API_KEY`/`LLM_BASE_URL` in `.env` — this script makes real LLM
calls (one per section, one per extracted item for verification; roughly 30–40 calls
for the full corpus with `gpt-4o-mini`, a few minutes and a small fraction of a US
cent). Raw results backing every number above:
`eval/results/extraction/2026-08-25/report.json` (Run 1) and
`eval/results/extraction/2026-08-25-v2/report.json` (Run 2).

---

*All quantitative results in Sections 3 and 5 are original measurements from the runs
described in Section 2; the diagnosis in Section 3.1 and the fix in Section 4 are
original analysis, verified by the Run 2 re-measurement, not assumed to have worked.*
