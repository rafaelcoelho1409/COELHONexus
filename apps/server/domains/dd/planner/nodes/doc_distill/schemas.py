"""doc_distill — per-doc semantic value object (Pydantic-validated)."""
from __future__ import annotations
from . import params

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# What kind of page this is, in the Diátaxis sense plus two roles chapter planning
# needs: `overview` (landing/index/TOC pages that mostly point at other pages) and
# `cross_cutting` (platform support, debugging/diagnostics, performance, threading
# or compatibility notes that apply across the whole framework, not to one topic).
DocRole = Literal["overview", "cross_cutting", "tutorial", "how_to", "reference", "explanation"]
DEFAULT_ROLE = "reference"



class DocDistillate(BaseModel):
    """Per-doc semantic representation for the LLM-first planner."""
    # Groq strict json_schema mode requires additionalProperties:false on every
    # object — extra="forbid" makes model_json_schema() emit it.
    model_config = ConfigDict(extra = "forbid")

    summary: str = Field(
        description = (
            f"{params.SUMMARY_WORDS_MIN}-{params.SUMMARY_WORDS_MAX} words. ONE sentence "
            f"describing what THIS file teaches/documents."
        ),
    )
    key_terms: list[str] = Field(
        description = (
            f"{params.KEY_TERMS_MIN}-{params.KEY_TERMS_MAX} technical identifiers "
            f"(function/class names, CLI commands, config keys, type names) "
            f"that appear in this doc."
        ),
    )
    role: DocRole = Field(
        default = DEFAULT_ROLE,
        description = "Page role; defaults to reference for fallback/legacy distillates.",
    )

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        n = len(s.split())
        if not (params.SUMMARY_WORDS_MIN <= n <= params.SUMMARY_WORDS_MAX):
            raise ValueError(
                f"summary must be {params.SUMMARY_WORDS_MIN}-{params.SUMMARY_WORDS_MAX} "
                f"words; got {n}"
            )
        return s

    @field_validator("key_terms")
    @classmethod
    def _validate_terms(cls, v: list[str]) -> list[str]:
        # Repair, don't reject: over-long lists are clipped to KEY_TERMS_MAX
        # and out-of-range / duplicate terms dropped; only a genuine
        # shortfall (< KEY_TERMS_MIN usable terms) raises.
        out: list[str] = []
        seen: set[str] = set()
        for t in v:
            s = " ".join(t.strip().split())
            if not (params.KEY_TERM_CHARS_MIN <= len(s) <= params.KEY_TERM_CHARS_MAX):
                continue
            k = s.casefold()
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
            if len(out) >= params.KEY_TERMS_MAX:
                break
        if len(out) < params.KEY_TERMS_MIN:
            raise ValueError(
                f"only {len(out)} usable unique key_terms "
                f"(minimum {params.KEY_TERMS_MIN})"
            )
        return out


class DocDistillateRaw(BaseModel):
    """What the LLM is asked to emit — same fields and guidance as
    `DocDistillate`, but NO word/term-count validators. Near misses (a 64-word
    summary, 2 usable key terms) used to be rejected whole — and because the
    chat layer wraps the ValidationError in a ChatError, the repair retry never
    even ran — so a good summary was thrown away for a content-free fallback
    (13/701 elasticsearch-python docs, 3/104 asyncio). `domain.normalize_distillate`
    clips/pads this into a valid `DocDistillate` instead."""
    model_config = ConfigDict(extra = "forbid")

    summary: str = Field(
        description = (
            f"{params.SUMMARY_WORDS_MIN}-{params.SUMMARY_WORDS_MAX} words. ONE sentence "
            f"describing what THIS file teaches/documents."
        ),
    )
    key_terms: list[str] = Field(
        description = (
            f"{params.KEY_TERMS_MIN}-{params.KEY_TERMS_MAX} technical identifiers "
            f"(function/class names, CLI commands, config keys, type names) "
            f"that appear in this doc."
        ),
    )
    role: DocRole = Field(
        description = (
            "What kind of page this is. overview = landing/index/table-of-contents page "
            "that mainly points at other pages; cross_cutting = platform support, "
            "debugging/diagnostics, performance, threading or compatibility notes "
            "that apply across the whole framework rather than to one topic; "
            "tutorial = guided lesson; how_to = task recipe; reference = API/option "
            "listing; explanation = concepts and design rationale."
        ),
    )
