"""checklist_eval types — Pydantic models for persisted + LLM-output side."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .constants import (
    CHECKLIST_SCHEMA_VERSION,
    CHECKLIST_PROMPT_VERSION,
    _FEEDBACK_MAX_CHARS,
    _FEEDBACK_MIN_CHARS,
)


# =============================================================================
# Pydantic — persisted side
# =============================================================================
class CriterionResult(BaseModel):
    """One checklist criterion's verdict. Binary, with a 1-sentence
    natural-language feedback string when failed (consumed by
    mgsr_replan as a repair instruction)."""
    name:     str
    passed:   bool
    kind:     Literal["deterministic", "llm_judge"]
    feedback: str = ""

    @field_validator("feedback")
    @classmethod
    def _validate_feedback(cls, v: str) -> str:
        s = " ".join((v or "").strip().split())
        # Allow empty when passed
        if s == "":
            return s
        if not (_FEEDBACK_MIN_CHARS <= len(s) <= _FEEDBACK_MAX_CHARS):
            # Truncate instead of reject — feedback length is best-effort
            return s[: _FEEDBACK_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"
        return s


class ChecklistEvaluation(BaseModel):
    """Full per-chapter checklist evaluation — persisted to MinIO."""
    schema_version: str = CHECKLIST_SCHEMA_VERSION
    prompt_version: str = CHECKLIST_PROMPT_VERSION
    chapter_id:     str
    chapter_title:  str
    framework_slug: str
    criteria:       list[CriterionResult]   # 12 entries (7 + 5)
    n_passed:       int
    n_total:        int
    pass_rate:      float
    chapter_passed: bool                    # pass_rate >= _PASS_THRESHOLD
    failed_feedback: list[str]              # extracted for mgsr_replan
    n_llm_judge_repairs: int = 0
    deployment_judge:    Optional[str] = None
    wall_ms:             Optional[int] = None


# =============================================================================
# Pydantic — LLM output side (private to this module)
# =============================================================================
class _LLMVerdict(BaseModel):
    """One verdict from the batched LLM-judge response."""
    passed:   bool
    feedback: str = ""

    @field_validator("feedback")
    @classmethod
    def _validate_feedback(cls, v: str) -> str:
        s = " ".join((v or "").strip().split())
        if s == "":
            return s
        if not (_FEEDBACK_MIN_CHARS <= len(s) <= _FEEDBACK_MAX_CHARS):
            return s[: _FEEDBACK_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"
        return s


class _LLMJudgePayload(BaseModel):
    """LLM-judge JSON response — one batched call returns all 5 verdicts.

    Field names MUST match the keys in `_LLM_CRITERIA` above; the prompt
    enforces the exact shape."""
    chapter_reads_coherently:           _LLMVerdict
    claims_grounded_in_sources:         _LLMVerdict
    terminology_consistent:             _LLMVerdict
    prose_code_first_not_meta_framing:  _LLMVerdict
    code_refs_introduced_in_prose:      _LLMVerdict
