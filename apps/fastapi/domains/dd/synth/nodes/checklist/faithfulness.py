"""Atomic-claim grounding — augments bundled LLM-judge's `claims_grounded_in_sources` via conservative-bias merge."""
from __future__ import annotations
from .keys import digest_latest_key, latest_blob_key, sawc_latest_key, versioned_blob_key
from .params import (
    DENSITY_MAX_AVG_EXPLANATION_WORDS,
    DENSITY_MAX_CHARS_PER_PARA,
    DENSITY_MIN_AVG_EXPLANATION_WORDS,
    DENSITY_MIN_CHARS_PER_PARA,
    FEEDBACK_MAX_CHARS,
    FEEDBACK_MIN_CHARS,
    LLM_CRITERIA,
    MAX_RENDERED_CHAPTER_CHARS,
    MIN_AVG_CODE_REFS_PER_SECTION,
    MIN_CITATIONS_PER_SECTION,
    MIN_CODE_REF_COVERAGE_FRACTION,
    PASS_THRESHOLD,
    PICKER_FALLBACK_RATE_MAX,
    REPAIR_RATE_MAX,
)
from .schemas import (
    ChecklistEvaluation,
    CriterionResult,
    LLMJudgePayload,
    LLMVerdict,
)
from .versions import CHECKLIST_PROMPT_VERSION, CHECKLIST_SCHEMA_VERSION

import asyncio
import json
import logging
import re
from hashlib import sha256

from domains.llm.rotator.chain import chat_judge_bandit_async

from ....ingestion.storage import get_storage


logger = logging.getLogger(__name__)

# Per-prose-hash cache: same prose + fixed prompt → same claims; re-runs hit MinIO instead of LLM.
_EXTRACT_PROMPT_VERSION = "v3-cache-2026-05-28"
_CLAIMS_CACHE_PREFIX = f"synth-cache/atomic-claims/{_EXTRACT_PROMPT_VERSION}"


def _prose_cache_key(prose: str) -> str:
    """16-hex sha256 of truncated prose (same truncation as LLM input, so key is semantically accurate)."""
    return sha256(prose.encode("utf-8")).hexdigest()[:16]


_EXTRACT_PROMPT = """Extract the atomic factual claims from this chapter prose.
An atomic claim is a single verifiable fact about the technology being documented.

Examples of valid claims:
  - "Library X uses Y as its default serialization format"
  - "The timeout parameter defaults to 30 seconds"
  - "Function foo returns a list of strings when called with bar = True"

NOT claims (skip these):
  - Generic motivation ("This makes the API easier to use")
  - Section transitions ("Now we will discuss...")
  - Structural statements ("This chapter covers three topics")

Return strict JSON. Cap at {max_claims} most-important claims.

--- CHAPTER PROSE (truncated to {prose_chars} chars) ---
{prose}
--- END PROSE ---

JSON: {{"claims": ["claim 1", "claim 2", ...]}}"""


_JUDGE_PROMPT = """Is the atomic claim at the END faithful to the source documentation?

A claim is SUPPORTED when ANY of these hold:
  (a) the source explicitly states it; OR
  (b) the source DEMONSTRATES it via code, example, or signature
      (e.g. "the snippet shows how to create a Browser instance" is
      supported when the source contains `Browser()` being instantiated);
      OR
  (c) the source trivially implies it from its API surface or shown
      behavior.

A claim is NOT supported when:
  - the source is silent AND the claim adds APIs/behavior not visible
    anywhere in the source; OR
  - the source contradicts the claim; OR
  - the claim invents specifics (parameter names, return types, error
    classes) absent from the source's text AND code.

Be charitable: code-first documentation often states facts BY
demonstrating them. Don't fail claims that the source backs through
example.

Answer in strict JSON: {{"supported": true | false, "evidence": "short quote OR symbol from source if supported, else empty"}}

--- SOURCE DOCUMENTATION (excerpt) ---
{source}
--- END SOURCE ---

CLAIM: {claim}"""


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_CONCURRENCY = 8
_MAX_CLAIMS = 30
_PROSE_CHARS = 12000
_SOURCE_CHARS = 12000
_EXTRACT_MAX_TOKENS = 1500
_JUDGE_MAX_TOKENS = 200
# chat_judge_bandit_async's own default (30s) was undersized — same fix
# as elsewhere in Synth (2026-09-06/07). Also directly relevant to issue
# #14: a call timeout here is a genuine judge-call failure and correctly
# feeds infra_degraded — but a timeout that would've succeeded with more
# headroom is a false positive, not a real signal.
_EXTRACT_TIMEOUT_S = 60.0
_JUDGE_TIMEOUT_S = 45.0
_MIN_CLAIMS_FOR_RUN = 1
# raised 0.60 → 0.75 (Run 5: judge flags code-demonstrated claims as unsupported when source TEXT doesn't restate; 0.75 still catches catastrophic hallucination ≥85%).
_MAX_UNSUPPORTED_RATIO = 0.75
# Below this fraction of claims actually judged (vs. fail-soft defaults from
# a broken call), the ratio above is noise, not signal — extraction/judge
# outages must not silently read as "verified faithful."
_MIN_EVALUATED_FRACTION = 0.5
# A fraction is only a meaningful signal once it's measured over enough
# trials — with 1-2 claims (small chapters), a single incidental judge-call
# blip already reads as 0% evaluated, indistinguishable from a real outage.
# Same "≥2, not 1" bar this codebase already uses for SUSTAINED_INFRA_
# OUTAGE_LIMIT: require at least this many actual call failures before the
# fraction floor above is trusted (issue #14, 2026-09-06 — confirmed live:
# a 1-section chapter's single timeout was folded into infra_degraded and
# fed the sustained-outage counter).
_MIN_ABSOLUTE_FAILURES_FOR_UNRESOLVED = 2

# Issue #20, 2026-09-07: temporarily disabled. Confirmed timing out on
# every single call observed across all three measured post-#17 runs
# (ch-01 x2, ch-02) — under current Rotator conditions this check is
# paying its full 45-60s cost on every checklist_eval iteration without
# completing even once. Fail-soft by design (can only ever downgrade the
# bundled judge's own verdict, never upgrade it), so disabling loses a
# currently-dormant safety net, not a working one — re-enable by
# flipping this back to True once a spot-check shows a real completion
# rate (see SYNTH-PERFORMANCE-ANALYSIS-2026-09-07.md).
ATOMIC_CLAIM_ENABLED = False


async def atomic_claim_grounding(
    *,
    chapter_prose: str,
    grounding_blob: str,
) -> dict:
    """Run atomic-claim grounding. Three outcomes, not two: passed=True
    (genuinely verified), passed=False (genuinely unsupported), or
    resolved=False (not enough real judge signal to say either —
    extraction crashed, or too many judge calls failed). Callers must
    treat resolved=False like a crash (defer to the bundled judge),
    never like a pass — collapsing "couldn't check" into "passed" is
    exactly what let a Rotator outage read as a clean grounding check."""
    if not ATOMIC_CLAIM_ENABLED:
        return {
            "passed": True, "resolved": True, "n_claims": 0,
            "n_evaluated": 0, "n_unsupported": 0, "unsupported_claims": [],
            "feedback": "", "method": "atomic_claim_disabled",
            "skip_reason": "disabled (issue #20) — see ATOMIC_CLAIM_ENABLED",
        }
    claims, extraction_ok = await _extract_claims(chapter_prose[:_PROSE_CHARS])
    if not extraction_ok:
        return {
            "passed": True, "resolved": False, "n_claims": 0,
            "n_evaluated": 0, "n_unsupported": 0, "unsupported_claims": [],
            "feedback": "", "method": "atomic_claim_v4",
            "skip_reason": "extraction_failed",
        }
    if len(claims) < _MIN_CLAIMS_FOR_RUN:
        # Genuinely nothing to verify — a real pass, not an outage artifact.
        return {
            "passed": True, "resolved": True, "n_claims": 0,
            "n_evaluated": 0, "n_unsupported": 0, "unsupported_claims": [],
            "feedback": "", "method": "atomic_claim_v4",
        }

    src = grounding_blob[:_SOURCE_CHARS]
    sem = asyncio.Semaphore(_CONCURRENCY)
    verdicts = await asyncio.gather(*[
        _judge_claim(sem, claim, src) for claim in claims
    ])

    n_claims = len(claims)
    n_call_failures = sum(1 for v in verdicts if v.get("_call_failed"))
    n_evaluated = n_claims - n_call_failures
    evaluated_fraction = n_evaluated / n_claims if n_claims else 0.0
    if n_call_failures:
        logger.warning(
            f"[atomic-claim-grounding] {n_call_failures}/{len(verdicts)} "
            f"judge calls failed — excluded from the verdict, not "
            f"defaulted to supported=True"
        )
    if (
        evaluated_fraction < _MIN_EVALUATED_FRACTION
        and n_call_failures >= _MIN_ABSOLUTE_FAILURES_FOR_UNRESOLVED
    ):
        logger.warning(
            f"[atomic-claim-grounding] only {n_evaluated}/{n_claims} claims "
            f"({evaluated_fraction:.0%}) got a real verdict — below the "
            f"{_MIN_EVALUATED_FRACTION:.0%} floor, treating as unresolved "
            f"rather than a genuine pass"
        )
        return {
            "passed": True, "resolved": False, "n_claims": n_claims,
            "n_evaluated": n_evaluated, "n_unsupported": 0,
            "unsupported_claims": [], "n_judge_call_failures": n_call_failures,
            "feedback": "", "method": "atomic_claim_v4",
            "skip_reason": "insufficient_evaluated_fraction",
        }

    # Denominator is EVALUATED claims only — a call failure must not be
    # able to dilute the ratio by masquerading as a "supported" claim.
    evaluated_pairs = [
        (claim, v) for claim, v in zip(claims, verdicts)
        if not v.get("_call_failed")
    ]
    unsupported = [
        {"claim": claim, "evidence": v.get("evidence", "")}
        for claim, v in evaluated_pairs
        if not v.get("supported", True)
    ]
    n_unsupported = len(unsupported)
    unsupported_ratio = n_unsupported / n_evaluated if n_evaluated else 0.0
    passed = unsupported_ratio <= _MAX_UNSUPPORTED_RATIO
    feedback = ""
    if not passed:
        sample = unsupported[0]["claim"][:160]
        feedback = (
            f"atomic-claim grounding: {n_unsupported}/{n_evaluated} claims "
            f"({unsupported_ratio:.0%}) not supported by source digest "
            f"(ceiling {_MAX_UNSUPPORTED_RATIO:.0%}); e.g. {sample!r}"
        )

    return {
        "passed": passed,
        "resolved": True,
        "n_claims": n_claims,
        "n_evaluated": n_evaluated,
        "n_unsupported": n_unsupported,
        "unsupported_ratio": round(unsupported_ratio, 3),
        "unsupported_claims": unsupported,
        "n_judge_call_failures": n_call_failures,
        "feedback": feedback,
        "method": "atomic_claim_v4",
    }


async def _extract_claims(prose: str) -> tuple[list[str], bool]:
    """Returns (claims, extraction_ok). extraction_ok=False means the LLM
    call/parse itself broke — distinct from a genuine "prose has 0 claims"
    result, since callers must not treat an outage as a trivial pass."""
    minio = get_storage()
    cache_key = f"{_CLAIMS_CACHE_PREFIX}/{_prose_cache_key(prose)}.json"
    try:
        if await minio.exists(cache_key):
            raw_text = await minio.read_text(cache_key)
            data = json.loads(raw_text or "{}")
            cached_claims = data.get("claims") or []
            if isinstance(cached_claims, list) and cached_claims:
                logger.info(
                    f"[atomic-claim-grounding] cache HIT — {len(cached_claims)} "
                    f"claims for prose key {cache_key.rsplit('/', 1)[-1]}"
                )
                return [
                    str(c).strip() for c in cached_claims
                    if isinstance(c, str) and c.strip()
                ][:_MAX_CLAIMS], True
    except Exception as e:
        logger.debug(
            f"[atomic-claim-grounding] cache read failed: "
            f"{type(e).__name__}: {e}"
        )

    try:
        prompt = _EXTRACT_PROMPT.format(
            max_claims = _MAX_CLAIMS, prose_chars = len(prose), prose = prose,
        )
        raw, _ = await chat_judge_bandit_async(
            prompt, max_tokens = _EXTRACT_MAX_TOKENS, temperature = 0.0,
            response_format = {"type": "json_object"},
            timeout_s = _EXTRACT_TIMEOUT_S,
        )
        m = _JSON_RE.search(raw or "")
        if not m:
            logger.warning(
                "[atomic-claim-grounding] extraction failed: "
                "no JSON object in response"
            )
            return [], False
        data = json.loads(m.group(0))
        claims = data.get("claims") or []
        # Sanitize: strings only, non-empty, capped
        out = [
            str(c).strip() for c in claims
            if isinstance(c, str) and c.strip()
        ][:_MAX_CLAIMS]
    except Exception as e:
        logger.warning(
            f"[atomic-claim-grounding] extraction failed: "
            f"{type(e).__name__}: {e}"
        )
        return [], False

    # Best-effort cache write.
    try:
        await minio.write(
            cache_key,
            json.dumps({"claims": out}, ensure_ascii = False),
            content_type = "application/json",
        )
    except Exception as e:
        logger.debug(
            f"[atomic-claim-grounding] cache write failed: "
            f"{type(e).__name__}: {e}"
        )
    return out, True


async def _judge_claim(
    sem: asyncio.Semaphore, claim: str, source: str,
) -> dict:
    """Verify ONE atomic claim against the source. Fail-soft: any failure
    returns supported = True so we don't override the bundled judge on
    infra hiccups — tagged _call_failed so the caller can tell a genuine
    pass apart from a silent default (this criterion's whole purpose is
    anti-hallucination, so rubber-stamping without a trace defeats it)."""
    async with sem:
        try:
            prompt = _JUDGE_PROMPT.format(claim = claim, source = source)
            raw, _ = await chat_judge_bandit_async(
                prompt, max_tokens = _JUDGE_MAX_TOKENS, temperature = 0.0,
                response_format = {"type": "json_object"},
                timeout_s = _JUDGE_TIMEOUT_S,
            )
            m = _JSON_RE.search(raw or "")
            if not m:
                logger.debug(
                    "[atomic-claim-grounding] judge response unparseable "
                    "— defaulting to supported=True"
                )
                return {"supported": True, "_call_failed": True}
            return json.loads(m.group(0))
        except Exception as e:
            logger.debug(
                f"[atomic-claim-grounding] judge call failed: "
                f"{type(e).__name__}: {e} — defaulting to supported=True"
            )
            return {"supported": True, "_call_failed": True}
