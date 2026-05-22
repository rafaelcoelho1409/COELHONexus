"""checklist_eval — Binary checklist evaluator library.

Pure module: Pydantic schemas + deterministic pre-gate functions +
prompt templates + LLM-judge response parser.
No I/O, no LLM calls — that lives in `synth/checklist/node.py`.

ARCHITECTURE — three published primitives layered

  1. CheckEval (arXiv 2403.18771, EMNLP 2025) — decompose subjective
     criteria into BINARY questions. Demonstrated **+0.45 avg inter-
     evaluator agreement** vs continuous Likert. Variance drops.
     Per-criterion explanations are inherently traceable (which is
     what mgsr_replan needs to act on).

  2. RefineBench (arXiv 2511.22173, Nov 2025) — 9.9 binary criteria
     per task on average; failed criteria emit natural-language
     feedback that becomes the NEXT-iteration prompt. We follow the
     same loop shape (checklist → guided feedback → mgsr_replan).

  3. Multi-layered evaluation pipeline (2026 industry consensus,
     e.g. Deterministic-vs-LLM Evaluators 2026 study):
       Layer 1: deterministic pre-gates — fail-fast, near-zero cost
       Layer 2: batched LLM-judge — one call for all semantic criteria
       Layer 3: (out of scope here) human sampling

WHAT IT REPLACES (deprecated 8-dim weighted grader)

  Deprecated grader:
    - 8 continuous 0.0-1.0 dimensions
    - weighted_score composite vs acceptance_threshold (default 0.85)
    - action enum: accept | refine | regenerate
    - Span-anchored Issue objects (span_quote + dimension + suggestion)
    - Problem: continuous scores have model-dependent calibration drift
      (a 0.85 from `glm-4.6` ≠ 0.85 from `gemini-2.5-flash`); judges
      disagreed on 0.7 vs 0.8 calls

  New checklist_eval:
    - 12 BINARY criteria (7 deterministic + 5 LLM-judge)
    - pass_rate = n_passed / n_total
    - chapter_passed if pass_rate >= 0.80
    - Failed criteria emit specific 1-sentence feedback strings →
      consumed by mgsr_replan as repair instructions

INPUTS / OUTPUTS

  Input (read by the node):
    sawc-latest.json       — written prose + coverage_stats
    digest-latest.json     — per_section grounding (key_facts) for the
                              LLM-judge's claims_grounded_in_sources check

  Output per criterion:
    CriterionResult{name, passed, kind, feedback}

  Output per chapter (persisted):
    ChecklistEvaluation{criteria, pass_rate, chapter_passed,
                         failed_feedback, ...}

DOWNSTREAM CONSUMER (next node):

  - `mgsr_replan` reads `failed_feedback` + the failed criterion names,
    emits structured replan actions on the outline DAG
    ({merge|delete|rename|reorder|add}) + halts via CoRefine confidence
    when pass_rate plateaus or budget exhausted.

TUNABLES

  _PASS_THRESHOLD              = 0.80
  _DENSITY_MIN_CHARS_PER_PARA  = 150
  _DENSITY_MAX_CHARS_PER_PARA  = 1200
  _REPAIR_RATE_MAX             = 0.50  (n_repairs / n_total_drafts_fired)
  _PICKER_FALLBACK_RATE_MAX    = 0.50
  _MIN_CITATIONS_PER_SECTION   = 1
  _MAX_RENDERED_CHAPTER_CHARS  = 60000  (cap for the LLM-judge prompt)
"""
from .constants import (
    CHECKLIST_PROMPT_VERSION,
    CHECKLIST_SCHEMA_VERSION,
    _DENSITY_MAX_CHARS_PER_PARA,
    _DENSITY_MIN_CHARS_PER_PARA,
    _FEEDBACK_MAX_CHARS,
    _FEEDBACK_MIN_CHARS,
    _LLM_CRITERIA,
    _MAX_RENDERED_CHAPTER_CHARS,
    _MIN_CITATIONS_PER_SECTION,
    _PASS_THRESHOLD,
    _PICKER_FALLBACK_RATE_MAX,
    _REPAIR_RATE_MAX,
)
from .types import (
    ChecklistEvaluation,
    CriterionResult,
    _LLMJudgePayload,
    _LLMVerdict,
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

__all__ = [
    # constants
    "CHECKLIST_PROMPT_VERSION",
    "CHECKLIST_SCHEMA_VERSION",
    "_DENSITY_MAX_CHARS_PER_PARA",
    "_DENSITY_MIN_CHARS_PER_PARA",
    "_FEEDBACK_MAX_CHARS",
    "_FEEDBACK_MIN_CHARS",
    "_LLM_CRITERIA",
    "_MAX_RENDERED_CHAPTER_CHARS",
    "_MIN_CITATIONS_PER_SECTION",
    "_PASS_THRESHOLD",
    "_PICKER_FALLBACK_RATE_MAX",
    "_REPAIR_RATE_MAX",
    # types
    "ChecklistEvaluation",
    "CriterionResult",
    "_LLMJudgePayload",
    "_LLMVerdict",
    # service
    "DETERMINISTIC_CHECKS",
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
    "collect_failed_feedback",
    "llm_payload_to_criteria",
    "render_chapter_for_judge",
    "render_digest_for_grounding",
]
