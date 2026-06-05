"""mgsr — pure helpers (halt cascade, validators, JSON parse, manifest
hash, fallback decision builder)."""
from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError

from .params import (
    CONFIDENCE_HIGH_THRESHOLD,
)
from .patterns import JSON_RE
from .schemas import (
    HaltReason,
    LLMReplanPayload,
    MGSRDecision,
    ReplanAction,
)
from .versions import MGSR_PROMPT_VERSION, MGSR_SCHEMA_VERSION


# Trivial-pass fast path
def is_trivial_pass(checklist: dict) -> bool:
    """Fast-path predicate: chapter already passed the checklist, no
    replan needed. Avoids the LLM call entirely.

    Per the SOTA doc threshold: pass_rate ≥ 0.80 is shippable.
    """
    if not checklist:
        return False
    if not bool(checklist.get("chapter_passed", False)):
        return False
    pass_rate = float(checklist.get("pass_rate", 0.0))
    return pass_rate >= 0.80


def build_trivial_pass_decision(pass_rate: float) -> MGSRDecision:
    """Construct the halt decision for the trivial-pass case."""
    return MGSRDecision(
        halt = True,
        halt_reason = "chapter_passed",
        confidence = 1.0,
        actions = [],
        rationale_overall = (
            f"Chapter passed checklist evaluator with pass_rate "
            f"{pass_rate:.2%} ≥ 0.80 threshold. No structural replan "
            f"needed; remaining failed criteria (if any) are acceptable "
            f"below the chapter-pass threshold."
        ),
    )


def fallback_decision(reason: str) -> MGSRDecision:
    """Conservative fallback when the LLM call fails irrecoverably.
    Emit no actions + halt the pipeline. Pipeline continues to
    render_audit_write with the current chapter as-is."""
    return MGSRDecision(
        halt = True,
        halt_reason = "confidence_high",  # conservative
        confidence = 0.5,
        actions = [],
        rationale_overall = (
            f"LLM-based replan unavailable: {reason}. Halting "
            f"conservatively to avoid blocking the pipeline; chapter "
            f"will be rendered as-is by render_audit_write. Operator "
            f"should review checklist_eval feedback manually."
        ),
    )


# Halt-reason derivation (when LLM was consulted)
def derive_halt_reason(
    payload: LLMReplanPayload,
    *,
    iteration: int = 0,
    budget: int = 5,
) -> tuple[bool, HaltReason]:
    """Combine the LLM's emitted `halt` flag with confidence-based and
    budget-based halt rules. Returns (halt, halt_reason).

    Halt cascade (in priority order):
      1. iteration ≥ budget → budget_exhausted
      2. confidence ≥ CONFIDENCE_HIGH_THRESHOLD → confidence_high
      3. halt==true + no actions → no_actions_needed
      4. halt==true + actions emitted → confidence_high (LLM said halt)
      5. Otherwise (v1) → v1_no_loop
    """
    if iteration >= budget:
        return True, "budget_exhausted"
    if payload.confidence >= CONFIDENCE_HIGH_THRESHOLD:
        return True, "confidence_high"
    if payload.halt and not payload.actions:
        return True, "no_actions_needed"
    if payload.halt:
        return True, "confidence_high"
    return True, "v1_no_loop"


# Cross-reference validators (post-Pydantic, fail-soft for repair loop)
def validate_actions_against_outline(
    actions: list[ReplanAction],
    *,
    valid_section_ids: set[str],
) -> list[str]:
    """Return list of issue strings suitable for repair-prompt feedback."""
    issues: list[str] = []
    available = set(valid_section_ids)

    for i, a in enumerate(actions):
        bad_targets = [t for t in a.targets if t not in available]
        if bad_targets:
            issues.append(
                f"action[{i}] ({a.action}): targets {bad_targets} are "
                f"not in the current outline OR were deleted by an "
                f"earlier action in this list."
            )
        if a.insert_after and a.insert_after not in available:
            issues.append(
                f"action[{i}] ({a.action}): insert_after "
                f"{a.insert_after!r} doesn't exist in the outline."
            )
        if a.insert_before and a.insert_before not in available:
            issues.append(
                f"action[{i}] ({a.action}): insert_before "
                f"{a.insert_before!r} doesn't exist in the outline."
            )
        if a.new_prerequisites:
            bad_prereqs = [
                p for p in a.new_prerequisites if p not in available
            ]
            if bad_prereqs:
                issues.append(
                    f"action[{i}] ({a.action}): new_prerequisites "
                    f"{bad_prereqs} don't exist in the outline."
                )

        # Simulate action's effect on `available` for next iteration
        if a.action == "delete":
            available -= set(a.targets)
        elif a.action == "merge":
            if len(a.targets) >= 2:
                available -= set(a.targets[1:])

    return issues


# JSON helpers
def parse_json_response(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _shorten_pydantic_error(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return "Pydantic validation failed (no detail)"
    lines = []
    for err in errs[:6]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}")
    suffix = f" (+{len(errs) - 6} more)" if len(errs) > 6 else ""
    return "; ".join(lines) + suffix


def try_parse_payload(
    raw: dict,
) -> tuple[Optional[LLMReplanPayload], Optional[str]]:
    try:
        return LLMReplanPayload.model_validate(raw), None
    except ValidationError as e:
        return None, _shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def compute_manifest_hash(
    *,
    checklist_manifest_hash: str,
    outline_manifest_hash: str,
) -> str:
    payload = (
        f"checklist={checklist_manifest_hash}|"
        f"outline={outline_manifest_hash}|"
        f"prompt={MGSR_PROMPT_VERSION}|"
        f"schema={MGSR_SCHEMA_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_mgsr_payload(text: str) -> dict:
    """Parse the persisted mgsr blob. render_audit_write checks
    `decision.halt` to know whether to render the current chapter as
    final (halt=true) or loop back (halt=false; v2 only)."""
    return json.loads(text)
