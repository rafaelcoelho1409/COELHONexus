"""checklist_eval — Pydantic schemas (LLM-judge output + persisted blob)."""
from __future__ import annotations
from . import params, versions

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator



class CriterionResult(BaseModel):
    """One criterion verdict; feedback string is consumed by mgsr_replan as a repair instruction."""
    name:     str
    passed:   bool
    kind:     Literal["deterministic", "llm_judge"]
    feedback: str = ""

    @field_validator("feedback")
    @classmethod
    def _validate_feedback(cls, v: str) -> str:
        s = " ".join((v or "").strip().split())
        if s == "":
            return s
        if not (params.FEEDBACK_MIN_CHARS <= len(s) <= params.FEEDBACK_MAX_CHARS):
            return s[: params.FEEDBACK_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"
        return s


class ChecklistEvaluation(BaseModel):
    """Full per-chapter checklist evaluation — persisted to MinIO."""
    schema_version: str = versions.CHECKLIST_SCHEMA_VERSION
    prompt_version: str = versions.CHECKLIST_PROMPT_VERSION
    chapter_id:     str
    chapter_title:  str
    framework_slug: str
    criteria:       list[CriterionResult]   # 12 entries (7 + 5)
    n_passed:       int
    n_total:        int
    pass_rate:      float
    chapter_passed: bool                    # pass_rate >= PASS_THRESHOLD
    failed_feedback: list[str]              # extracted for mgsr_replan
    n_llm_judge_repairs: int = 0
    deployment_judge:    Optional[str] = None
    wall_ms:             Optional[int] = None


class LLMVerdict(BaseModel):
    """One verdict from the batched LLM-judge response."""
    passed:   bool
    feedback: str = ""

    @field_validator("feedback")
    @classmethod
    def _validate_feedback(cls, v: str) -> str:
        s = " ".join((v or "").strip().split())
        if s == "":
            return s
        if not (params.FEEDBACK_MIN_CHARS <= len(s) <= params.FEEDBACK_MAX_CHARS):
            return s[: params.FEEDBACK_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"
        return s


class LLMJudgePayload(BaseModel):
    """LLM-judge JSON response; field names MUST match keys in `LLM_CRITERIA`."""
    chapter_reads_coherently:           LLMVerdict
    claims_grounded_in_sources:         LLMVerdict
    terminology_consistent:             LLMVerdict
    prose_code_first_not_meta_framing:  LLMVerdict
    code_refs_introduced_in_prose:      LLMVerdict


class CocoaAbstraction(BaseModel):
    """One code block's behavioral abstraction (CoCoA stage 1)."""
    model_config = ConfigDict(extra = "forbid")

    id: str = Field(description = "The integer id from the input, as a string.")
    spec: str = Field(description = "1-sentence behavioral abstraction naming key identifiers.")


class CocoaExplainerResponse(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    abstractions: list[CocoaAbstraction]


class CocoaVerdict(BaseModel):
    """One prose/code alignment verdict (CoCoA stage 2). `reason` is a
    required field (not optional) — OpenAI strict json_schema mode
    requires every property in `required`; making it genuinely required
    (empty string allowed when aligned=true) avoids the `strict=False`
    fallback, which was observed live to let reasoning text leak into
    `id` for at least one call."""
    model_config = ConfigDict(extra = "forbid")

    id: str = Field(description = "The integer id from the input, as a string.")
    aligned: bool
    reason: str = Field(
        description = "Short string naming the drift; empty string when aligned=true.",
    )


class CocoaJudgeResponse(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    verdicts: list[CocoaVerdict]


class AtomicClaimExtraction(BaseModel):
    """Atomic factual claims extracted from chapter prose."""
    model_config = ConfigDict(extra = "forbid")

    claims: list[str]


class AtomicClaimJudge(BaseModel):
    """One atomic-claim grounding verdict against source material.
    `evidence` required (not optional) — see CocoaVerdict for why."""
    model_config = ConfigDict(extra = "forbid")

    supported: bool
    evidence: str = Field(
        description = "Short quote or symbol from source if supported, else empty string.",
    )
