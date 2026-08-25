"""Split a document's normalized text into `Section`s for per-section extraction.

Two strategies, tried in order: clause-header regex (readable section labels, e.g.
"ماده ۱۲"), falling back to fixed-size windowing (`processor.chunk_text`, reused
unmodified) whenever headers are sparse or absent. The regex pass is a readability
bonus, not the coverage mechanism — real PDFs rarely have clean, consistent headings,
so windowing is what actually guarantees every character of the document is covered.
"""

from __future__ import annotations

import re
from itertools import pairwise

from app.documents.processor import chunk_text
from app.extraction.models import Section

# Persian clause markers (ماده=article, بند=clause, تبصره=note, فصل=chapter) and their
# English equivalents, plus bare numbered headings ("12.1 ...", "3) ..."). Matched at
# the start of a line, case-insensitive for the English forms. A colon is allowed on
# either side of the number: real RTL PDF extraction (bidi reordering of a neutral
# punctuation mark next to a digit run — see eval/README.md's documented artifact)
# routinely produces "ماده :۱" as well as the logical "ماده ۱:".
_CLAUSE_HEADER = re.compile(
    r"^\s*(?:"
    r"(?:ماده|بند|تبصره|فصل)\s*[:：]?\s*[۰-۹0-9]+"
    r"|(?:article|clause|section)\s+\d+(?:\.\d+)*"
    r"|\d+(?:\.\d+)*[.)]\s"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# Below this density of detected headers per 1000 characters, the regex pass is
# considered too sparse to trust as the sole sectioning mechanism — windowing takes
# over instead of leaving most of the document as one giant, unlabeled section.
_MIN_HEADERS_PER_1000_CHARS = 0.3

_WINDOW_SIZE = 5000
_WINDOW_OVERLAP = 500


def sectionize(text: str, *, document_id: str) -> list[Section]:
    """Split `text` (already `processor.normalize_text`-normalized) into sections.

    Never returns an empty list for non-empty `text` — falls back to a single
    windowed section if even windowing produces nothing (text shorter than one
    window), and to `[]` only when `text` itself is empty (the caller's signal for
    "nothing to extract," matching `processor.process_document`'s own convention).
    """
    if not text.strip():
        return []

    headers = list(_CLAUSE_HEADER.finditer(text))
    density = len(headers) / (len(text) / 1000) if text else 0.0
    if density >= _MIN_HEADERS_PER_1000_CHARS:
        return _sections_from_headers(text, headers, document_id=document_id)
    return _sections_from_windows(text, id_prefix=f"{document_id}-w")


def _sections_from_headers(
    text: str, headers: list[re.Match[str]], *, document_id: str
) -> list[Section]:
    sections: list[Section] = []
    boundaries = [m.start() for m in headers]
    if boundaries[0] > 0:
        boundaries = [0, *boundaries]
    boundaries.append(len(text))

    for position, (start, end) in enumerate(pairwise(boundaries)):
        block = text[start:end].strip()
        if not block:
            continue
        label = None
        header_match = next((m for m in headers if m.start() == start), None)
        if header_match is not None:
            label = block.splitlines()[0].strip()
        section_id = f"{document_id}-s{position:04d}"
        if len(block) > _WINDOW_SIZE:
            sections.extend(
                _sections_from_windows(
                    block, id_prefix=f"{section_id}-w", label_prefix=label
                )
            )
            continue
        sections.append(Section(section_id=section_id, label=label, text=block))
    return sections


def _sections_from_windows(
    text: str, *, id_prefix: str, label_prefix: str | None = None
) -> list[Section]:
    windows = chunk_text(text, chunk_size=_WINDOW_SIZE, chunk_overlap=_WINDOW_OVERLAP)
    return [
        Section(
            section_id=f"{id_prefix}{position:04d}",
            label=label_prefix,
            text=window,
        )
        for position, window in enumerate(windows)
    ]
