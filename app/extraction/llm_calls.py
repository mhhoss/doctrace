"""The only two LLM call sites in the extraction pipeline. Each is a thin wrapper
around `llm.structured_predict` — module-level functions, not methods, so tests can
`monkeypatch.setattr(llm_calls, "extract_section_items", ...)` exactly as this
project's existing tests monkeypatch `build_embedding_model`/`build_llm` at their call
sites, instead of needing a fake LLM that correctly emulates tool-calling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from llama_index.core.prompts import PromptTemplate

from app.extraction.models import SectionExtractionResult, VerificationResult

if TYPE_CHECKING:
    from llama_index.core.llms import LLM

_EXTRACTION_PROMPT = PromptTemplate(
    "You are reviewing one section of a contract/tender/policy document. Extract "
    "every discrete obligation, requirement, deadline, or prohibition stated in this "
    "section — not summaries, not interpretations, only what the text actually "
    "states. For each one, give a short self-contained statement and a verbatim "
    "quote (copied exactly, not paraphrased) from the section text that supports it. "
    "Write the statement in the SAME language as the section text below — never "
    "translate it, even if you would naturally respond in a different language. "
    "If the section states nothing extractable, return an empty list.\n\n"
    "Section{label_suffix}:\n{section_text}"
)

_VERIFICATION_PROMPT = PromptTemplate(
    "Does the following passage actually support the claim, exactly as stated, "
    "without extrapolation, assumption, or unstated conditions? Answer strictly "
    "based on what the passage says.\n\n"
    "Claim: {claim}\n\n"
    "Passage (the claim's surrounding source section):\n{passage}"
)


def extract_section_items(
    llm: LLM, *, section_text: str, section_label: str | None
) -> SectionExtractionResult:
    label_suffix = f' ("{section_label}")' if section_label else ""
    return llm.structured_predict(
        SectionExtractionResult,
        _EXTRACTION_PROMPT,
        section_text=section_text,
        label_suffix=label_suffix,
    )


def verify_item(llm: LLM, *, claim: str, passage: str) -> VerificationResult:
    return llm.structured_predict(
        VerificationResult, _VERIFICATION_PROMPT, claim=claim, passage=passage
    )
