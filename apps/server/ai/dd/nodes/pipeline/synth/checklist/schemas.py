"""checklist_eval — Pydantic schemas (LLM-judge output + persisted blob)."""
from __future__ import annotations
from . import params, versions

import json
from typing import Literal, Optional

from pydantic import BaseModel, field_validator, model_validator



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
    chapter_passed: bool                    # score threshold plus every required hard gate
    failed_feedback: list[str]              # extracted for mgsr_replan
    n_llm_judge_repairs: int = 0
    judge_unavailable:   bool = False        # the judge never answered; criteria are the deterministic gates only
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

    @model_validator(mode = "before")
    @classmethod
    def _unwrap_stringified_verdicts(cls, data):
        """Some judge models double-encode each verdict as a JSON string ('{"passed": true, "feedback": ""}'); asyncio ch-04 lost its whole
        LLM review to 5 validation errors of this kind. A string value that parses to an object is used as that object."""
        if not isinstance(data, dict):
            return data
        out = {}
        for k, v in data.items():
            if isinstance(v, str):
                try:
                    j = json.loads(v)
                except ValueError:
                    j = None
                if isinstance(j, dict):
                    v = j
            out[k] = v
        return out


