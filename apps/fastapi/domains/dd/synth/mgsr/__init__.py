"""mgsr_replan subpackage — LLM-driven repair-plan decision."""
from .domain import (
    build_trivial_pass_decision,
    compute_manifest_hash,
    derive_halt_reason,
    fallback_decision,
    is_trivial_pass,
    load_mgsr_payload,
    parse_json_response,
    try_parse_payload,
    validate_actions_against_outline,
)
from .params import (
    CONFIDENCE_HIGH_THRESHOLD,
    MAX_ACTIONS_PER_REPLAN,
)
from .prompts import build_repair_prompt, build_replan_prompt
from .schemas import (
    HaltReason,
    LLMReplanPayload,
    MGSRDecision,
    MGSRReplan,
    REPLAN_RESPONSE_FORMAT,
    ReplanAction,
)
from .versions import MGSR_PROMPT_VERSION, MGSR_SCHEMA_VERSION


__all__ = [
    "CONFIDENCE_HIGH_THRESHOLD",
    "HaltReason",
    "LLMReplanPayload",
    "MAX_ACTIONS_PER_REPLAN",
    "MGSRDecision",
    "MGSRReplan",
    "MGSR_PROMPT_VERSION",
    "MGSR_SCHEMA_VERSION",
    "REPLAN_RESPONSE_FORMAT",
    "ReplanAction",
    "build_repair_prompt",
    "build_replan_prompt",
    "build_trivial_pass_decision",
    "compute_manifest_hash",
    "derive_halt_reason",
    "fallback_decision",
    "is_trivial_pass",
    "load_mgsr_payload",
    "parse_json_response",
    "try_parse_payload",
    "validate_actions_against_outline",
]
