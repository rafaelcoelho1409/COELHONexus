"""checklist_eval — Binary checklist evaluator (CheckEval + RefineBench + 3-layer eval).

12 binary criteria (7 deterministic + 5 LLM-judge). Replaces the
deprecated 8-dim weighted grader. Failed criteria emit specific
natural-language feedback strings consumed by mgsr_replan as repair
instructions.

See docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md.
"""
from .node import checklist_eval
from .schemas import (
    ChecklistEvaluation,
    CriterionResult,
    LLMJudgePayload,
    LLMVerdict,
)
from .service import (
    DETERMINISTIC_CHECKS,
    aggregate_pass_rate,
    build_judge_prompt,
    build_repair_prompt,
    check_all_sections_cite_at_least_1,
    check_all_sections_present,
    check_density_within_bounds,
    check_no_placeholder_sections,
    check_picker_fallback_rate_low,
    check_repair_rate_low,
    check_unique_headings,
    collect_failed_feedback,
    llm_payload_to_criteria,
    render_chapter_for_judge,
    render_digest_for_grounding,
)
from .versions import CHECKLIST_PROMPT_VERSION, CHECKLIST_SCHEMA_VERSION


__all__ = [
    "CHECKLIST_PROMPT_VERSION",
    "CHECKLIST_SCHEMA_VERSION",
    "ChecklistEvaluation",
    "CriterionResult",
    "DETERMINISTIC_CHECKS",
    "LLMJudgePayload",
    "LLMVerdict",
    "aggregate_pass_rate",
    "build_judge_prompt",
    "build_repair_prompt",
    "check_all_sections_cite_at_least_1",
    "check_all_sections_present",
    "check_density_within_bounds",
    "check_no_placeholder_sections",
    "check_picker_fallback_rate_low",
    "check_repair_rate_low",
    "check_unique_headings",
    "checklist_eval",
    "collect_failed_feedback",
    "llm_payload_to_criteria",
    "render_chapter_for_judge",
    "render_digest_for_grounding",
]
