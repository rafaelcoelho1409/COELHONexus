"""Atomic-claim grounding check — augments the bundled LLM-judge's
`claims_grounded_in_sources` verdict (2026-05-24).

WHY THIS EXISTS

The bundled checklist LLM-judge (`build_judge_prompt` in service.py) checks
five binary criteria — including `claims_grounded_in_sources` — in a SINGLE
LLM call. The grounding criterion specifically asks the judge to "spot-check
3-5 citations against the per-section grounding". This is bounded by the
judge's token budget for ALL 5 criteria; long chapters get only a coarse
PASS/FAIL signal.

This module adds a SEPARATE atomic-claim grounding check that:
  1. Extracts atomic factual claims from the chapter prose (1 bandit LLM call)
  2. Verifies each claim individually against the source digest (N parallel
     bandit LLM calls, bounded concurrency)
  3. Returns (verdict, n_claims, n_unsupported, unsupported_claims[])

The result AUGMENTS (not replaces) the bundled judge's verdict using
conservative-bias aggregation: if either the atomic check OR the bundled
judge fails, the criterion fails. This composes with the existing fail-soft
pipeline — never makes results worse.

CONSTRAINT: free-tier-only. No paid APIs. No local inference inside COELHO
Cloud. All LLM calls flow through the FGTS-VA bandit-routed rotator
(chat_judge_bandit_async). See project_local_vs_rotator_architecture.

Per `feedback_kd_quality_over_speed`: tokens are free; ~5-30 extra calls per
chapter is trivial cost for the +8-12pp expected F1 lift over cosine baseline
and 2-3pp delta below the (architecturally-banned) LettuceDetect-large.

EMPIRICAL BASELINE (free-tier on LLM-AggreFact):
  Mistral-Large-2 ........... 76.5%
  NIM Llama-3.3-70B ......... ~75%
  Free-tier bandit ensemble.. expected 76-78%
  vs LettuceDetect-large .... 79.22 (banned by constraint)
  vs current cosine ......... ~65-68% on technical content
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from domains.llm.rotator.chain import chat_judge_bandit_async


logger = logging.getLogger(__name__)


_EXTRACT_PROMPT = """Extract the atomic factual claims from this chapter prose.
An atomic claim is a single verifiable fact about the technology being documented.

Examples of valid claims:
  - "Library X uses Y as its default serialization format"
  - "The timeout parameter defaults to 30 seconds"
  - "Function foo returns a list of strings when called with bar=True"

NOT claims (skip these):
  - Generic motivation ("This makes the API easier to use")
  - Section transitions ("Now we will discuss...")
  - Structural statements ("This chapter covers three topics")

Return strict JSON. Cap at {max_claims} most-important claims.

--- CHAPTER PROSE (truncated to {prose_chars} chars) ---
{prose}
--- END PROSE ---

JSON: {{"claims": ["claim 1", "claim 2", ...]}}"""


_JUDGE_PROMPT = """Is this atomic claim supported by the source documentation below?

CLAIM: {claim}

--- SOURCE DOCUMENTATION (excerpt) ---
{source}
--- END SOURCE ---

A claim is SUPPORTED if the source explicitly states it or trivially implies it.
A claim is NOT supported if the source is silent on it, contradicts it, or only
loosely relates without backing the specific assertion.

Answer in strict JSON: {{"supported": true | false, "evidence": "short quote from source if supported, else empty"}}"""


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_CONCURRENCY = 8
_MAX_CLAIMS = 30
_PROSE_CHARS = 12000
_SOURCE_CHARS = 12000
_EXTRACT_MAX_TOKENS = 1500
_JUDGE_MAX_TOKENS = 200
_MIN_CLAIMS_FOR_RUN = 1


async def atomic_claim_grounding(
    *,
    chapter_prose: str,
    grounding_blob: str,
) -> dict:
    """Run the atomic-claim grounding check.

    Args:
      chapter_prose: The full rendered chapter text (markdown).
      grounding_blob: The per-section digest grounding (key_facts) — same blob
        the bundled LLM-judge sees.

    Returns:
      {
        "passed":              bool,    # True if zero unsupported claims
        "n_claims":            int,
        "n_unsupported":       int,
        "unsupported_claims":  [{"claim": str, "evidence": str}, ...],
        "feedback":            str,     # 1-sentence summary for mgsr_replan
        "method":              "atomic_claim_v1",
      }

    Fail-soft: any extraction or verification failure defaults to "supported"
    so the bundled judge's verdict isn't overridden by infrastructure flakes.
    """
    claims = await _extract_claims(chapter_prose[:_PROSE_CHARS])
    if len(claims) < _MIN_CLAIMS_FOR_RUN:
        # Trivially-pass: nothing to verify.
        return {
            "passed": True, "n_claims": 0, "n_unsupported": 0,
            "unsupported_claims": [], "feedback": "",
            "method": "atomic_claim_v1",
        }

    src = grounding_blob[:_SOURCE_CHARS]
    sem = asyncio.Semaphore(_CONCURRENCY)
    verdicts = await asyncio.gather(*[
        _judge_claim(sem, claim, src) for claim in claims
    ])

    unsupported = [
        {"claim": claim, "evidence": v.get("evidence", "")}
        for claim, v in zip(claims, verdicts)
        if not v.get("supported", True)
    ]
    n_claims = len(claims)
    n_unsupported = len(unsupported)
    feedback = ""
    if n_unsupported > 0:
        sample = unsupported[0]["claim"][:160]
        feedback = (
            f"atomic-claim grounding: {n_unsupported}/{n_claims} claims "
            f"not supported by source digest (e.g. {sample!r})"
        )

    return {
        "passed": n_unsupported == 0,
        "n_claims": n_claims,
        "n_unsupported": n_unsupported,
        "unsupported_claims": unsupported,
        "feedback": feedback,
        "method": "atomic_claim_v1",
    }


async def _extract_claims(prose: str) -> list[str]:
    try:
        prompt = _EXTRACT_PROMPT.format(
            max_claims=_MAX_CLAIMS, prose_chars=len(prose), prose=prose,
        )
        raw, _ = await chat_judge_bandit_async(
            prompt, max_tokens=_EXTRACT_MAX_TOKENS, temperature=0.0,
        )
        m = _JSON_RE.search(raw or "")
        if not m:
            return []
        data = json.loads(m.group(0))
        claims = data.get("claims") or []
        # Sanitize: strings only, non-empty, capped
        return [
            str(c).strip() for c in claims
            if isinstance(c, str) and c.strip()
        ][:_MAX_CLAIMS]
    except Exception as e:
        logger.warning(
            f"[atomic-claim-grounding] extraction failed: "
            f"{type(e).__name__}: {e}"
        )
        return []


async def _judge_claim(
    sem: asyncio.Semaphore, claim: str, source: str,
) -> dict:
    """Verify ONE atomic claim against the source. Fail-soft: any failure
    returns supported=True so we don't override the bundled judge on
    infra hiccups."""
    async with sem:
        try:
            prompt = _JUDGE_PROMPT.format(claim=claim, source=source)
            raw, _ = await chat_judge_bandit_async(
                prompt, max_tokens=_JUDGE_MAX_TOKENS, temperature=0.0,
            )
            m = _JSON_RE.search(raw or "")
            if not m:
                return {"supported": True}
            return json.loads(m.group(0))
        except Exception:
            return {"supported": True}
