"""Deterministic, LLM-free string matching: locating a quote's source page, and
collapsing near-duplicate items produced by the windowed-fallback sectionizer's
overlapping windows. Both are plain string comparison — no embeddings, no second LLM
call — the item counts here (hundreds of pages/items at most) don't justify either.
"""

from __future__ import annotations

import difflib
import re

from app.extraction.models import ExtractedItem

_WHITESPACE = re.compile(r"\s+")

# A quote is considered located if the longest common substring with a candidate page
# (or adjacent-page pair, for a quote straddling a page break) covers at least this
# fraction of the quote's own length.
_LOCATE_THRESHOLD = 0.8

# Two items in the same category are treated as duplicates above this text-similarity
# ratio (`difflib.SequenceMatcher.ratio`).
_DEDUP_THRESHOLD = 0.85


def _collapse(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _containment(needle: str, haystack: str) -> float:
    if not needle:
        return 0.0
    matcher = difflib.SequenceMatcher(None, needle, haystack, autojunk=False)
    match = matcher.find_longest_match(0, len(needle), 0, len(haystack))
    return match.size / len(needle)


def locate_quote(pages: list[str], quote: str) -> tuple[int, int] | None:
    """Find which 1-indexed page(s) `quote` came from, or None if unlocatable.

    Tries an exact substring match per page first, then a same-page fuzzy match, then
    adjacent-page pairs (a clause can straddle a page break) — in that order, so the
    cheapest and most certain check always wins when it succeeds.
    """
    needle = _collapse(quote)
    if not needle:
        return None

    collapsed_pages = [_collapse(page) for page in pages]

    for index, page in enumerate(collapsed_pages, start=1):
        if needle in page:
            return (index, index)

    best_page, best_ratio = None, 0.0
    for index, page in enumerate(collapsed_pages, start=1):
        ratio = _containment(needle, page)
        if ratio > best_ratio:
            best_page, best_ratio = index, ratio
    if best_page is not None and best_ratio >= _LOCATE_THRESHOLD:
        return (best_page, best_page)

    for index in range(len(collapsed_pages) - 1):
        combined = f"{collapsed_pages[index]} {collapsed_pages[index + 1]}"
        if needle in combined:
            return (index + 1, index + 2)

    return None


def deduplicate(items: list[ExtractedItem]) -> list[ExtractedItem]:
    """Drop near-duplicate items within the same category, keeping each group's
    first occurrence (document order) — a stable, deterministic tie-break."""
    survivors: list[ExtractedItem] = []
    by_category: dict[str, list[ExtractedItem]] = {}
    for item in items:
        by_category.setdefault(item.category, []).append(item)

    for group in by_category.values():
        kept: list[ExtractedItem] = []
        for item in group:
            normalized = _collapse(item.text.lower())
            if any(
                difflib.SequenceMatcher(
                    None, normalized, _collapse(k.text.lower()), autojunk=False
                ).ratio()
                >= _DEDUP_THRESHOLD
                for k in kept
            ):
                continue
            kept.append(item)
        survivors.extend(kept)
    return survivors
