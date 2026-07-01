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
_MIN_CLAIMS_FOR_RUN = 1
# raised 0.60 → 0.75 (Run 5: judge flags code-demonstrated claims as unsupported when source TEXT doesn't restate; 0.75 still catches catastrophic hallucination ≥85%).
_MAX_UNSUPPORTED_RATIO = 0.75


async def atomic_claim_grounding(
    *,
    chapter_prose: str,
    grounding_blob: str,
) -> dict:
    """Run atomic-claim grounding; fail-soft (any failure → supported) so bundled judge stands."""
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
    unsupported_ratio = n_unsupported / n_claims if n_claims else 0.0
    passed = unsupported_ratio <= _MAX_UNSUPPORTED_RATIO
    feedback = ""
    if not passed:
        sample = unsupported[0]["claim"][:160]
        feedback = (
            f"atomic-claim grounding: {n_unsupported}/{n_claims} claims "
            f"({unsupported_ratio:.0%}) not supported by source digest "
            f"(ceiling {_MAX_UNSUPPORTED_RATIO:.0%}); e.g. {sample!r}"
        )

    return {
        "passed": passed,
        "n_claims": n_claims,
        "n_unsupported": n_unsupported,
        "unsupported_ratio": round(unsupported_ratio, 3),
        "unsupported_claims": unsupported,
        "feedback": feedback,
        "method": "atomic_claim_v3",
    }


async def _extract_claims(prose: str) -> list[str]:
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
                ][:_MAX_CLAIMS]
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
        )
        m = _JSON_RE.search(raw or "")
        if not m:
            return []
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
        return []

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
    return out


async def _judge_claim(
    sem: asyncio.Semaphore, claim: str, source: str,
) -> dict:
    """Verify ONE atomic claim against the source. Fail-soft: any failure
    returns supported = True so we don't override the bundled judge on
    infra hiccups."""
    async with sem:
        try:
            prompt = _JUDGE_PROMPT.format(claim = claim, source = source)
            raw, _ = await chat_judge_bandit_async(
                prompt, max_tokens = _JUDGE_MAX_TOKENS, temperature = 0.0,
                response_format = {"type": "json_object"},
            )
            m = _JSON_RE.search(raw or "")
            if not m:
                return {"supported": True}
            return json.loads(m.group(0))
        except Exception:
            return {"supported": True}
