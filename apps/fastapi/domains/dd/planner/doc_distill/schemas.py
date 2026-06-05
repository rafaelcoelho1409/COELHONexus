"""doc_distill — per-doc semantic value object (Pydantic-validated)."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .params import (
    KEY_TERM_CHARS_MAX,
    KEY_TERM_CHARS_MIN,
    KEY_TERMS_MAX,
    KEY_TERMS_MIN,
    SUMMARY_WORDS_MAX,
    SUMMARY_WORDS_MIN,
)


class DocDistillate(BaseModel):
    """Per-doc semantic representation for the LLM-first planner."""
    summary: str = Field(
        description = (
            f"{SUMMARY_WORDS_MIN}-{SUMMARY_WORDS_MAX} words. ONE sentence "
            f"describing what THIS file teaches/documents."
        ),
    )
    key_terms: list[str] = Field(
        description = (
            f"{KEY_TERMS_MIN}-{KEY_TERMS_MAX} technical identifiers "
            f"(function/class names, CLI commands, config keys, type names) "
            f"that appear in this doc."
        ),
    )

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        n = len(s.split())
        if not (SUMMARY_WORDS_MIN <= n <= SUMMARY_WORDS_MAX):
            raise ValueError(
                f"summary must be {SUMMARY_WORDS_MIN}-{SUMMARY_WORDS_MAX} "
                f"words; got {n}"
            )
        return s

    @field_validator("key_terms")
    @classmethod
    def _validate_terms(cls, v: list[str]) -> list[str]:
        if not (KEY_TERMS_MIN <= len(v) <= KEY_TERMS_MAX):
            raise ValueError(
                f"key_terms count must be {KEY_TERMS_MIN}-{KEY_TERMS_MAX}; "
                f"got {len(v)}"
            )
        out: list[str] = []
        seen: set[str] = set()
        for t in v:
            s = " ".join(t.strip().split())
            if not (KEY_TERM_CHARS_MIN <= len(s) <= KEY_TERM_CHARS_MAX):
                raise ValueError(
                    f"key_term length must be {KEY_TERM_CHARS_MIN}-"
                    f"{KEY_TERM_CHARS_MAX}; got {len(s)}"
                )
            k = s.casefold()
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
        if len(out) < KEY_TERMS_MIN:
            raise ValueError(
                f"after dedup, only {len(out)} unique key_terms "
                f"(minimum {KEY_TERMS_MIN})"
            )
        return out


DISTILL_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "doc_distillate",
        "schema": DocDistillate.model_json_schema(),
        "strict": False,
    },
}
