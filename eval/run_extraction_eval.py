"""Extraction-quality benchmark: runs `app/extraction/engine.py` against a small,
hand-labeled corpus and reports recall, precision, citation accuracy, and grounding-
verifier accuracy — not just "does it run."

Uses the real, configured LLM (`app.config.get_settings()` / `build_llm`) — the same
client the application itself would use — so results reflect actual model behavior,
not a stub. Requires a working `LLM_API_KEY`/`LLM_BASE_URL` in `.env`; fails loudly if
one is not configured (`require_credentials`).

Ground-truth matching is automatic (fuzzy text similarity) with every match written to
the report for a human to spot-check — the matching itself is a judgment call and
should not be trusted blindly (see eval/EXTRACTION_BENCHMARK.md's limitations section).

Each run writes to its own `eval/results/extraction/<run-tag>/` directory (default:
today's date), for the same reason `eval/run_benchmark.py --run-tag` does: a re-run
must never silently overwrite a prior run's numbers.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from llama_index.core.llms import LLM

from app.config import build_llm, get_settings, require_credentials
from app.extraction.engine import extract_document
from app.extraction.models import ExtractedItem, ExtractionOutcome

EVAL_DIR = Path(__file__).resolve().parent
CORPUS_DIR = EVAL_DIR / "extraction_corpus"
RESULTS_DIR = EVAL_DIR / "results" / "extraction"

# A candidate is considered a match for a ground-truth item above this text-similarity
# ratio, same metric and same threshold app/extraction/matching.py already uses for
# dedup — one consistent notion of "close enough" across the whole pipeline.
MATCH_THRESHOLD = 0.55


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower(), autojunk=False).ratio()


@dataclass(frozen=True)
class GroundTruthItem:
    doc_id: str
    category: str
    text: str
    page: int | None


def load_ground_truth() -> list[GroundTruthItem]:
    data = json.loads((CORPUS_DIR / "ground_truth.json").read_text(encoding="utf-8"))
    return [
        GroundTruthItem(
            doc_id=item["doc_id"],
            category=item["category"],
            text=item["text"],
            page=item["page"],
        )
        for item in data["items"]
    ]


def load_manifest() -> list[dict]:
    return json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))


def run_document(doc: dict, llm: LLM) -> tuple[ExtractionOutcome, float]:
    path = CORPUS_DIR / f"{doc['doc_id']}.{doc['format']}"
    content = path.read_bytes()
    t0 = time.perf_counter()
    outcome = extract_document(
        document_id=doc["doc_id"],
        filename=path.name,
        file_type=doc["format"],
        content=content,
        llm=llm,
        verify=True,
    )
    elapsed = time.perf_counter() - t0
    return outcome, elapsed


def match_items(
    ground_truth: list[GroundTruthItem], extracted: list[ExtractedItem]
) -> dict:
    """Greedy one-to-one matching: each ground-truth item claims its best-scoring
    unclaimed extracted item, if any clears MATCH_THRESHOLD. Returns matches (for
    human spot-checking), unmatched ground truth (recall misses), and unmatched
    extracted items (precision misses / possible hallucinations)."""
    claimed_extracted: set[str] = set()
    matches = []
    unmatched_gt = []

    for gt in ground_truth:
        candidates = [
            item
            for item in extracted
            if item.document_id == gt.doc_id and item.item_id not in claimed_extracted
        ]
        best_item, best_score = None, 0.0
        for item in candidates:
            score = _similarity(gt.text, item.text)
            if score > best_score:
                best_item, best_score = item, score
        if best_item is not None and best_score >= MATCH_THRESHOLD:
            claimed_extracted.add(best_item.item_id)
            page_correct = _page_correct(gt.page, best_item)
            matches.append(
                {
                    "doc_id": gt.doc_id,
                    "ground_truth_text": gt.text,
                    "ground_truth_page": gt.page,
                    "extracted_text": best_item.text,
                    "extracted_category": best_item.category,
                    "extracted_page_start": best_item.page_start,
                    "extracted_page_end": best_item.page_end,
                    "similarity": round(best_score, 3),
                    "category_correct": best_item.category == gt.category,
                    "page_correct": page_correct,
                    "verification_status": best_item.verification_status.value,
                }
            )
        else:
            unmatched_gt.append({"doc_id": gt.doc_id, "text": gt.text, "page": gt.page})

    unmatched_extracted = [
        {"doc_id": item.document_id, "text": item.text, "category": item.category}
        for item in extracted
        if item.item_id not in claimed_extracted
    ]
    return {
        "matches": matches,
        "unmatched_ground_truth": unmatched_gt,
        "unmatched_extracted": unmatched_extracted,
    }


def _page_correct(expected_page: int | None, item: ExtractedItem) -> bool | None:
    """None means "not applicable" (DOCX/TXT, or an unlocated PDF item) — excluded
    from the citation-accuracy denominator, not counted as wrong."""
    if expected_page is None:
        return None
    if item.page_start is None:
        return False
    return item.page_start <= expected_page <= (item.page_end or item.page_start)


def compute_metrics(match_result: dict, ground_truth: list[GroundTruthItem]) -> dict:
    matches = match_result["matches"]
    n_gt = len(ground_truth)
    n_matched = len(matches)
    recall = n_matched / n_gt if n_gt else None

    n_extracted_total = n_matched + len(match_result["unmatched_extracted"])
    precision = n_matched / n_extracted_total if n_extracted_total else None

    category_correct = sum(1 for m in matches if m["category_correct"])
    category_accuracy = category_correct / n_matched if n_matched else None

    page_applicable = [m for m in matches if m["page_correct"] is not None]
    page_correct = sum(1 for m in page_applicable if m["page_correct"])
    citation_accuracy = page_correct / len(page_applicable) if page_applicable else None

    verified = sum(1 for m in matches if m["verification_status"] == "verified")
    verification_rate = verified / n_matched if n_matched else None

    return {
        "n_ground_truth": n_gt,
        "n_matched": n_matched,
        "n_unmatched_ground_truth": len(match_result["unmatched_ground_truth"]),
        "n_unmatched_extracted": len(match_result["unmatched_extracted"]),
        "recall": recall,
        "precision": precision,
        "category_accuracy": category_accuracy,
        "citation_accuracy": citation_accuracy,
        "citation_accuracy_n": len(page_applicable),
        "verification_rate": verification_rate,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-tag",
        default=datetime.datetime.now(tz=datetime.UTC).date().isoformat(),
    )
    args = parser.parse_args()
    out_dir = RESULTS_DIR / args.run_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    require_credentials(settings)
    llm = build_llm(settings)
    print(f"Using LLM: {settings.llm_model} via {settings.llm_base_url}")

    ground_truth = load_ground_truth()
    manifest = load_manifest()

    all_outcomes = []
    all_match_results = {"matches": [], "unmatched_ground_truth": [], "unmatched_extracted": []}
    per_doc_timing = {}

    for doc in manifest:
        print(f"\n=== {doc['doc_id']} ({doc['format']}, {doc['language']}) ===")
        outcome, elapsed = run_document(doc, llm)
        all_outcomes.append(outcome)
        per_doc_timing[doc["doc_id"]] = round(elapsed, 2)
        print(
            f"  status={outcome.status.value} sections={outcome.sections_succeeded}/"
            f"{outcome.sections_total} items={len(outcome.items)} time={elapsed:.1f}s"
        )
        doc_gt = [g for g in ground_truth if g.doc_id == doc["doc_id"]]
        doc_match = match_items(doc_gt, outcome.items)
        for key, values in all_match_results.items():
            values.extend(doc_match[key])

    metrics = compute_metrics(all_match_results, ground_truth)
    print("\n=== Metrics ===")
    for key, value in metrics.items():
        print(f"  {key}: {value}")

    report = {
        "run_tag": args.run_tag,
        "llm_model": settings.llm_model,
        "llm_base_url": settings.llm_base_url,
        "metrics": metrics,
        "per_document_timing_seconds": per_doc_timing,
        "matches": all_match_results["matches"],
        "unmatched_ground_truth": all_match_results["unmatched_ground_truth"],
        "unmatched_extracted": all_match_results["unmatched_extracted"],
        "outcomes": [
            {
                "document_id": o.document_id,
                "filename": o.filename,
                "status": o.status.value,
                "sections_total": o.sections_total,
                "sections_succeeded": o.sections_succeeded,
                "sections_failed": o.sections_failed,
                "item_count": len(o.items),
                "section_errors": [
                    {"section_id": s.section_id, "error": s.error}
                    for s in o.sections
                    if s.error
                ],
            }
            for o in all_outcomes
        ],
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nWrote report to {out_dir / 'report.json'}")


if __name__ == "__main__":
    main()
