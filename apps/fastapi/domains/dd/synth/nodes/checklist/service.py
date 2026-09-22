"""checklist_eval service — deterministic pre-gates, aggregation, rendering,
prompt builders, LLM verdict coercion, CoCoA two-stage alignment check, and
atomic-claim grounding."""
from __future__ import annotations
import domains
from . import domain, keys, params, patterns, prompts, schemas, versions
# For best-seen promotion — needs sawc's OWN versioned-key convention,
# not checklist's (each node's versioned_blob_key hardcodes its own path
# segment). See the best-seen fix below for why this lives here.

import asyncio
import json
import logging
import random
import time
from typing import Optional


logger = logging.getLogger(__name__)


def _emit_criterion_scores(framework: str, criteria: list[dict | schemas.CriterionResult]) -> None:
    for criterion in criteria:
        if isinstance(criterion, schemas.CriterionResult):
            name = criterion.name
            passed = criterion.passed
        else:
            name = str((criterion or {}).get("name") or "")
            passed = bool((criterion or {}).get("passed", False))
        if not name:
            continue
        domains.dd.synth.runtime.observability.metrics.record_grader_dim_score(
            framework = framework,
            dim = name,
            score = 1.0 if passed else 0.0,
        )


async def _run_llm_judge(
    *,
    thread_id: str,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    sawc: dict,
    digest: dict,
    rendered_chapter: str,
    rendered_digest: str,
    truncated: bool,
) -> tuple[list[schemas.CriterionResult], Optional[str], bool, int]:
    """Fire batched judge → parse → repair if needed; hard failure → conservative FAILED fallback."""
    t0 = time.monotonic()
    cur_rendered_chapter = rendered_chapter
    cur_rendered_digest = rendered_digest
    cur_truncated = truncated

    deployment: Optional[str] = None
    response: Optional[str] = None
    last_error: Optional[Exception] = None
    for call_attempt in range(params.MAX_CALL_ATTEMPTS):
        prompt = domain.build_judge_prompt(
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            framework=framework,
            rendered_chapter=cur_rendered_chapter,
            rendered_digest=cur_rendered_digest,
            truncated=cur_truncated,
        )
        try:
            response, meta = await domains.settings.chat.service.chat_text_async(
                prompt,
                max_tokens=_MAX_TOKENS_JUDGE,
                temperature=_TEMPERATURE_JUDGE,
                response_format=schemas.JUDGE_RESPONSE_FORMAT,
                timeout_s=_TIMEOUT_S_JUDGE,
            )
            deployment = (meta or {}).get("deployment")
            last_error = None
            break
        except Exception as e:
            last_error = e
            if call_attempt < params.MAX_CALL_ATTEMPTS - 1:
                # The Rotator's own cascade already exhausted — a retry
                # mostly helps against a transient whole-pool wave. If
                # this looks like context overflow (60K chars ≈ 15K
                # tokens can still exceed a small-context arm from the
                # heterogeneous pool), re-render at half budget first so
                # the retry doesn't just reproduce the same failure —
                # cheap insurance against triggering a full mgsr_replan
                # cycle for what was really an infra hiccup.
                if domain.is_context_overflow_error(e):
                    cur_rendered_chapter, cur_truncated = (
                        domain.render_chapter_for_judge(
                            sawc,
                            char_cap=params.MAX_RENDERED_CHAPTER_CHARS // 2,
                        )
                    )
                    cur_rendered_digest = domain.render_digest_for_grounding(
                        digest, char_cap=10_000,
                    )
                await asyncio.sleep(1.0 + random.random())
    if last_error is not None:
        wall_ms = int((time.monotonic() - t0) * 1000)
        logger.warning(
            f"[checklist_eval] LLM judge call failed after "
            f"{params.MAX_CALL_ATTEMPTS} attempt(s): "
            f"{type(last_error).__name__}: {last_error}"
        )
        return (
            domain.fallback_llm_verdicts(f"{type(last_error).__name__}"),
            None, False, wall_ms,
        )

    parsed = domain.parse_json_response(response)
    payload: Optional[schemas.LLMJudgePayload] = None
    err: Optional[str] = None
    repaired = False

    if parsed is not None:
        payload, err = domain.try_parse_judge(parsed)

    # One repair attempt if parse OR Pydantic failed
    if payload is None and _MAX_REPAIR_ATTEMPTS > 0:
        repair_issues = [
            err if err else "previous response was not parseable JSON"
        ]
        current_json = json.dumps(parsed or {"_raw": (response or "")[:400]})
        repair_prompt = domain.build_repair_prompt(
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            framework=framework,
            rendered_chapter=cur_rendered_chapter,
            rendered_digest=cur_rendered_digest,
            truncated=cur_truncated,
            current_json=current_json,
            issues=repair_issues,
        )
        try:
            rr, rm = await domains.settings.chat.service.chat_text_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
                response_format=schemas.JUDGE_RESPONSE_FORMAT,
                timeout_s=_TIMEOUT_S_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = domain.parse_json_response(rr)
            if rp is not None:
                payload, err = domain.try_parse_judge(rp)
                if payload is not None:
                    repaired = True
        except Exception as e:
            logger.warning(
                f"[checklist_eval] LLM judge repair failed: "
                f"{type(e).__name__}: {e}"
            )
            # 2026-09-08: `deployment` was still holding the main call's
            # value here (set at line ~733, before repair was even needed)
            # — so a repair-call timeout fell through to the fallback-FAIL
            # return below with a non-None deployment, making
            # `judge_call_failed = deployment is None` wrongly False.
            # Confirmed live on the ch-05 retest: this exact shape (main
            # call unparseable, repair call times out) recorded a pure
            # infra failure as a genuine content judgment, undermining both
            # the no-recovery-floor RETHINK protection and the
            # consecutive_infra_degraded sustained-outage counter (issue
            # #14's whole mechanism). The main-call-failure path a few
            # lines up already returns None correctly — this mirrors it.
            deployment = None

    wall_ms = int((time.monotonic() - t0) * 1000)

    if payload is None:
        logger.warning(
            f"[checklist_eval] LLM judge unparseable after repair "
            f"({err}); using fallback FAIL verdicts"
        )
        return (
            domain.fallback_llm_verdicts(f"judge_parse_failed: {err}"),
            deployment, False, wall_ms,
        )

    return domain.llm_payload_to_criteria(payload), deployment, repaired, wall_ms

_TEMPERATURE_JUDGE      = 0.0

_TEMPERATURE_REPAIR     = 0.0

_MAX_TOKENS_JUDGE       = 3000

_MAX_TOKENS_REPAIR      = 3000

# chat_text_async's own default (30s) was undersized — same fix
# as outline/digest/sawc (2026-09-06/07). A failed bundled-judge call
# here directly feeds `infra_degraded` (issues #10/#14), so a timeout
# that would have succeeded with more headroom was actively corrupting
# the sustained-outage signal, not just costing one bad iteration.
_TIMEOUT_S_JUDGE        = 90.0
_TIMEOUT_S_REPAIR       = 90.0

_MAX_REPAIR_ATTEMPTS    = 1

# Issue #22 (2026-09-08): the two result-persistence writes below have no
# bounded timeout and no log line either side — during the ch-05 retest,


# ---------------------------------------------------------------------------
# CoCoA two-stage alignment check (arXiv 2410.03131) — overrides c11+c12 on drift.
# ---------------------------------------------------------------------------

async def _cocoa_read_cached_abstraction(minio, h: str) -> str | None:
    """Return cached spec for hash `h`, or None on miss/error."""
    key = keys.cocoa_abstraction_key(h)
    try:
        if not await minio.exists(key):
            return None
        raw = await minio.read_text(key)
        obj = json.loads(raw)
        spec = (obj.get("spec") or "").strip()
        return spec or None
    except Exception:
        return None


async def _cocoa_write_cached_abstraction(minio, h: str, spec: str) -> None:
    """Best-effort cache write. Failures are silent — the abstraction
    still works for this run, we just miss the cache for future ones."""
    if not h or not spec:
        return
    key = keys.cocoa_abstraction_key(h)
    try:
        await minio.write(
            key,
            json.dumps({"spec": spec}),
            content_type = "application/json",
        )
    except Exception as e:
        logger.debug(
            f"[cocoa] cache write failed for {h[:8]}…: "
            f"{type(e).__name__}: {e}"
        )


async def _cocoa_explain_blocks(blocks: list[dict]) -> dict[str, str]:
    """Run the explainer on a batch of code blocks; derived blocks skip cache (body varies per run)."""
    if not blocks:
        return {}

    minio = domains.dd.ingestion.storage.service.get_storage()

    # derived blocks (code_source='derived') always go to the LLM; verbatim blocks cache by hash.
    cached: dict[str, str] = {}
    misses: list[dict] = []
    for b in blocks:
        h = (b.get("hash") or "").strip()
        is_derived = (b.get("code_source") or "verbatim") == "derived"
        if h and not is_derived:
            spec = await _cocoa_read_cached_abstraction(minio, h)
            if spec:
                cached[b["id"]] = spec
                continue
        misses.append(b)

    if cached:
        logger.info(
            f"[cocoa] explainer cache: {len(cached)}/{len(blocks)} "
            f"hits ({len(misses)} miss); LLM call will cover misses"
        )

    # If everything was cached, skip the LLM call entirely.
    if not misses:
        return cached

    prompt = prompts.COCOA_EXPLAINER_PROMPT.format(
        blocks_block = domain.render_blocks_for_explainer(misses),
    )
    try:
        response, _ = await domains.settings.chat.service.chat_text_async(
            prompt,
            max_tokens = params.COCOA_EXPLAINER_MAX_TOKENS,
            temperature = params.COCOA_EXPLAINER_TEMPERATURE,
            response_format = {"type": "json_object"},
            timeout_s = params.COCOA_EXPLAINER_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(
            f"[cocoa] explainer call failed: {type(e).__name__}: {e}"
        )
        return cached    # ship whatever we had cached; bundled judge stands
    parsed = domain.parse_json(response or "")
    if not parsed:
        return cached
    fresh: dict[str, str] = {}
    for row in (parsed.get("abstractions") or []):
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        spec = str(row.get("spec") or "").strip()
        if rid and spec:
            fresh[rid] = spec

    # Derived blocks are unsafe to cache by hash (body varies).
    miss_by_id = {b["id"]: b for b in misses}
    for rid, spec in fresh.items():
        b = miss_by_id.get(rid)
        if not b:
            continue
        h = (b.get("hash") or "").strip()
        is_derived = (b.get("code_source") or "verbatim") == "derived"
        if h and not is_derived:
            await _cocoa_write_cached_abstraction(minio, h, spec)

    return {**cached, **fresh}


async def _cocoa_judge_pairs(pairs: list[dict]) -> dict[str, dict]:
    """Returns {id_str: {"aligned": bool, "reason": str}}. Failures fall
    through to {} — missing ids are excluded from both the numerator and
    denominator by the caller (n_judged = len(verdicts)), not defaulted to
    either aligned or misaligned."""
    if not pairs:
        return {}
    prompt = prompts.COCOA_JUDGE_PROMPT.format(
        pairs_block = domain.render_pairs_for_judge(pairs),
    )
    try:
        response, _ = await domains.settings.chat.service.chat_text_async(
            prompt,
            max_tokens = params.COCOA_JUDGE_MAX_TOKENS,
            temperature = params.COCOA_JUDGE_TEMPERATURE,
            response_format = {"type": "json_object"},
            timeout_s = params.COCOA_JUDGE_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(
            f"[cocoa] judge call failed: {type(e).__name__}: {e}"
        )
        return {}
    parsed = domain.parse_json(response or "")
    if not parsed:
        return {}
    out: dict[str, dict] = {}
    for row in (parsed.get("verdicts") or []):
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        if not rid:
            continue
        out[rid] = {
            "aligned": bool(row.get("aligned")),
            "reason": str(row.get("reason") or "").strip(),
        }
    return out


async def cocoa_alignment_check(
    *,
    sawc_payload: dict,
    vault: dict[str, str],
) -> dict:
    """Run CoCoA two-stage alignment over every (subtopic, code) pair; fail-soft → passes."""
    if not params.COCOA_ENABLED:
        return {
            "passed":         True,
            "resolved":       True,  # deliberate skip, not an infra failure
            "method":         "cocoa_disabled",
            "n_pairs":        0,
            "n_aligned":      0,
            "n_misaligned":   0,
            "alignment_rate": 1.0,
            "misaligned":     [],
            "feedback":       "cocoa temporarily disabled (issue #20) — see params.COCOA_ENABLED",
        }
    sections = sawc_payload.get("sections") or []

    # Stable integer id so JSON round-trips are robust.
    pairs: list[dict] = []   # input rows the LLM stages consume
    for s in sections:
        sub_list = s.get("subtopics") or []
        for st in sub_list:
            if not isinstance(st, dict):
                continue
            subheading = (st.get("subheading") or "").strip()
            explanation = (st.get("explanation") or "").strip()
            h = (st.get("code_ref_hash") or "").strip()
            code_source = st.get("code_source") or "verbatim"
            derived = (st.get("derived_code") or "").strip()
            if code_source == "derived" and derived:
                body = derived
                lang = "python"
            else:
                body = vault.get(h, "") or ""
                # vault entries include fences — strip them for the
                # explainer prompt (cleaner abstraction).
                body = domain.strip_fences(body)
                lang = domain.detect_lang(vault.get(h, ""))
            if not (subheading and explanation and body):
                continue
            pairs.append({
                "id":          str(len(pairs)),
                "hash":        h,            # for stage-1 per-hash cache
                "code_source": code_source,  # derived blocks skip cache
                "subheading":  subheading,
                "explanation": explanation,
                "lang":        lang,
                "body":        body,
                "section_id":  s.get("section_id", "?"),
            })

    n_pairs = len(pairs)
    if n_pairs == 0:
        return {
            "passed":         True,
            "resolved":       True,   # genuinely nothing to check, not an outage
            "method":         "cocoa_skipped",
            "n_pairs":        0,
            "n_aligned":      0,
            "n_misaligned":   0,
            "alignment_rate": 1.0,
            "misaligned":     [],
            "feedback":       "no subtopics with both code body + prose",
        }

    # Zero-identifier overlap → auto-flagged misaligned (CC ch-01: 6 cases caught without LLM calls).
    structural_misaligned: list[dict] = []
    pairs_for_llm: list[dict] = []
    for p in pairs:
        if domain.has_keyword_overlap(
            code_body = p.get("body", ""),
            explanation = p.get("explanation", ""),
        ):
            pairs_for_llm.append(p)
        else:
            structural_misaligned.append({
                "section_id": p.get("section_id"),
                "subheading": p["subheading"],
                "reason": (
                    "explanation shares zero informative identifiers "
                    "with the cited code body (structural pre-check); "
                    "prose is talking about a different API"
                ),
            })
    if structural_misaligned:
        logger.info(
            f"[cocoa] keyword-overlap pre-check flagged "
            f"{len(structural_misaligned)}/{n_pairs} pairs as structurally "
            f"misaligned; LLM judge will only see "
            f"{len(pairs_for_llm)} pairs"
        )
    pairs = pairs_for_llm

    # Slice by COCOA_MAX_SUBTOPICS_PER_BATCH so prompts don't balloon.
    batches: list[list[dict]] = [
        pairs[i:i + params.COCOA_MAX_SUBTOPICS_PER_BATCH]
        for i in range(0, n_pairs, params.COCOA_MAX_SUBTOPICS_PER_BATCH)
    ]

    specs: dict[str, str] = {}
    for batch in batches:
        blocks = [
            {
                "id":          p["id"],
                "hash":        p.get("hash") or "",
                "code_source": p.get("code_source") or "verbatim",
                "lang":        p["lang"],
                "body":        p["body"],
            }
            for p in batch
        ]
        partial = await _cocoa_explain_blocks(blocks)
        specs.update(partial)

    if not specs:
        return {
            "passed":         True,    # fail-soft — don't override bundled
            "resolved":       False,
            "method":         "cocoa_skipped",
            "n_pairs":        n_pairs,
            "n_aligned":      n_pairs,
            "n_misaligned":   0,
            "alignment_rate": 1.0,
            "misaligned":     [],
            "feedback":       "cocoa explainer failed; bundled judge stands",
        }

    # Judge across all pairs that received an abstraction.
    judge_input: list[dict] = []
    for p in pairs:
        spec = specs.get(p["id"])
        if not spec:
            continue
        judge_input.append({
            "id":          p["id"],
            "spec":        spec,
            "subheading":  p["subheading"],
            "explanation": p["explanation"],
        })

    verdicts: dict[str, dict] = {}
    for i in range(0, len(judge_input), params.COCOA_MAX_SUBTOPICS_PER_BATCH):
        batch = judge_input[i:i + params.COCOA_MAX_SUBTOPICS_PER_BATCH]
        partial = await _cocoa_judge_pairs(batch)
        verdicts.update(partial)

    # Evaluated-fraction floor over the LLM stage specifically — pairs that
    # never got a spec (explainer failure) or never got a verdict (judge
    # failure) must not silently count toward "misaligned" just by being
    # absent from `verdicts`. n_pairs here is len(pairs_for_llm) (post
    # structural pre-check), i.e. exactly what SHOULD have reached the judge.
    n_llm_total = len(pairs)
    n_llm_judged = len(verdicts)
    llm_evaluated_fraction = (
        n_llm_judged / n_llm_total if n_llm_total else 1.0
    )
    if (
        llm_evaluated_fraction < params.COCOA_MIN_EVALUATED_FRACTION
        and (n_llm_total - n_llm_judged) >= params.COCOA_MIN_ABSOLUTE_GAP_FOR_UNRESOLVED
    ):
        logger.warning(
            f"[cocoa] only {n_llm_judged}/{n_llm_total} LLM-stage pairs "
            f"({llm_evaluated_fraction:.0%}) got a real verdict — below "
            f"the {params.COCOA_MIN_EVALUATED_FRACTION:.0%} floor, treating as "
            f"unresolved rather than computing a rate over missing evidence"
        )
        return {
            "passed":         True,
            "resolved":       False,
            "method":         "cocoa_skipped",
            "n_pairs":        n_pairs,
            "n_aligned":      n_pairs,
            "n_misaligned":   0,
            "alignment_rate": 1.0,
            "misaligned":     [],
            "feedback":       "cocoa judge under-evaluated; bundled judge stands",
        }

    n_aligned = 0
    misaligned: list[dict] = list(structural_misaligned)   # U5 merge
    by_id = {p["id"]: p for p in pairs}
    for pid, v in verdicts.items():
        p = by_id.get(pid)
        if p is None:
            continue
        if v.get("aligned", True):
            n_aligned += 1
        else:
            misaligned.append({
                "section_id": p.get("section_id"),
                "subheading": p["subheading"],
                "reason": v.get("reason") or "explanation does not ground to the cited code",
            })

    # Denominator is structural pre-check pairs + pairs that ACTUALLY got a
    # judge verdict — not n_pairs, which would let un-judged pairs (explainer
    # or judge call failures) silently drag the rate down as if misaligned.
    n_judged = len(structural_misaligned) + n_llm_judged
    rate = (n_aligned / n_judged) if n_judged else 1.0
    passed = rate >= params.COCOA_ALIGN_PASS_FRACTION

    feedback = ""
    if not passed:
        sample = [
            f"{m['subheading']!r} ({m['reason'][:80]})"
            for m in misaligned[:3]
        ]
        feedback = (
            f"CoCoA: {n_aligned}/{n_judged} subtopics aligned "
            f"({rate:.0%}; floor {params.COCOA_ALIGN_PASS_FRACTION:.0%}). "
            f"Sample drift: {sample}. mgsr_replan should re-roll those "
            f"sections with stronger code-grounded prose."
        )

    return {
        "passed":         passed,
        "resolved":       True,
        "method":         "cocoa_v1",
        "n_pairs":        n_pairs,
        "n_judged":       n_judged,
        "n_aligned":      n_aligned,
        "n_misaligned":   len(misaligned),
        "alignment_rate": rate,
        "misaligned":     misaligned[:50],   # cap for blob size
        "feedback":       feedback,
    }


# ---------------------------------------------------------------------------
# Atomic-claim grounding — augments bundled LLM-judge's `claims_grounded_in_sources` via conservative-bias merge.
# ---------------------------------------------------------------------------


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
    if not params.ATOMIC_CLAIM_ENABLED:
        return {
            "passed": True, "resolved": True, "n_claims": 0,
            "n_evaluated": 0, "n_unsupported": 0, "unsupported_claims": [],
            "feedback": "", "method": "atomic_claim_disabled",
            "skip_reason": "disabled (issue #20) — see params.ATOMIC_CLAIM_ENABLED",
        }
    claims, extraction_ok = await _atomic_claim_extract_claims(
        chapter_prose[:params.ATOMIC_CLAIM_PROSE_CHARS],
    )
    if not extraction_ok:
        return {
            "passed": True, "resolved": False, "n_claims": 0,
            "n_evaluated": 0, "n_unsupported": 0, "unsupported_claims": [],
            "feedback": "", "method": "atomic_claim_v4",
            "skip_reason": "extraction_failed",
        }
    if len(claims) < params.ATOMIC_CLAIM_MIN_CLAIMS_FOR_RUN:
        # Genuinely nothing to verify — a real pass, not an outage artifact.
        return {
            "passed": True, "resolved": True, "n_claims": 0,
            "n_evaluated": 0, "n_unsupported": 0, "unsupported_claims": [],
            "feedback": "", "method": "atomic_claim_v4",
        }

    src = grounding_blob[:params.ATOMIC_CLAIM_SOURCE_CHARS]
    sem = asyncio.Semaphore(params.ATOMIC_CLAIM_CONCURRENCY)
    verdicts = await asyncio.gather(*[
        _atomic_claim_judge_claim(sem, claim, src) for claim in claims
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
        evaluated_fraction < params.ATOMIC_CLAIM_MIN_EVALUATED_FRACTION
        and n_call_failures >= params.ATOMIC_CLAIM_MIN_ABSOLUTE_FAILURES_FOR_UNRESOLVED
    ):
        logger.warning(
            f"[atomic-claim-grounding] only {n_evaluated}/{n_claims} claims "
            f"({evaluated_fraction:.0%}) got a real verdict — below the "
            f"{params.ATOMIC_CLAIM_MIN_EVALUATED_FRACTION:.0%} floor, treating as unresolved "
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
    passed = unsupported_ratio <= params.ATOMIC_CLAIM_MAX_UNSUPPORTED_RATIO
    feedback = ""
    if not passed:
        sample = unsupported[0]["claim"][:160]
        feedback = (
            f"atomic-claim grounding: {n_unsupported}/{n_evaluated} claims "
            f"({unsupported_ratio:.0%}) not supported by source digest "
            f"(ceiling {params.ATOMIC_CLAIM_MAX_UNSUPPORTED_RATIO:.0%}); e.g. {sample!r}"
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


async def _atomic_claim_extract_claims(prose: str) -> tuple[list[str], bool]:
    """Returns (claims, extraction_ok). extraction_ok=False means the LLM
    call/parse itself broke — distinct from a genuine "prose has 0 claims"
    result, since callers must not treat an outage as a trivial pass."""
    minio = domains.dd.ingestion.storage.service.get_storage()
    cache_key = keys.atomic_claim_key(domain.prose_cache_key(prose))
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
                ][:params.ATOMIC_CLAIM_MAX_CLAIMS], True
    except Exception as e:
        logger.debug(
            f"[atomic-claim-grounding] cache read failed: "
            f"{type(e).__name__}: {e}"
        )

    try:
        prompt = prompts.ATOMIC_CLAIM_EXTRACT_PROMPT.format(
            max_claims = params.ATOMIC_CLAIM_MAX_CLAIMS, prose_chars = len(prose), prose = prose,
        )
        raw, _ = await domains.settings.chat.service.chat_text_async(
            prompt, max_tokens = params.ATOMIC_CLAIM_EXTRACT_MAX_TOKENS, temperature = 0.0,
            response_format = {"type": "json_object"},
            timeout_s = params.ATOMIC_CLAIM_EXTRACT_TIMEOUT_S,
        )
        m = patterns.ATOMIC_CLAIM_JSON_RE.search(raw or "")
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
        ][:params.ATOMIC_CLAIM_MAX_CLAIMS]
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


async def _atomic_claim_judge_claim(
    sem: asyncio.Semaphore, claim: str, source: str,
) -> dict:
    """Verify ONE atomic claim against the source. Fail-soft: any failure
    returns supported = True so we don't override the bundled judge on
    infra hiccups — tagged _call_failed so the caller can tell a genuine
    pass apart from a silent default (this criterion's whole purpose is
    anti-hallucination, so rubber-stamping without a trace defeats it)."""
    async with sem:
        try:
            prompt = prompts.ATOMIC_CLAIM_JUDGE_PROMPT.format(claim = claim, source = source)
            raw, _ = await domains.settings.chat.service.chat_text_async(
                prompt, max_tokens = params.ATOMIC_CLAIM_JUDGE_MAX_TOKENS, temperature = 0.0,
                response_format = {"type": "json_object"},
                timeout_s = params.ATOMIC_CLAIM_JUDGE_TIMEOUT_S,
            )
            m = patterns.ATOMIC_CLAIM_JSON_RE.search(raw or "")
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


async def checklist_eval_run(state: domains.dd.synth.state.SynthState) -> dict:
    """Run the binary checklist evaluator for one chapter."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "checklist_path":  "",
            "checklist_stats": {
                "skipped": "no_slug_or_chapter_id", "wall_ms": 0,
            },
            "status": "failed",
            "error":  "framework_slug or chapter_id missing from SynthState",
        }

    t0 = time.monotonic()
    minio = domains.dd.ingestion.storage.service.get_storage()

    sawc_key = keys.sawc_latest_key(slug, chapter_id)
    digest_key = keys.digest_latest_key(slug, chapter_id)

    if not await minio.exists(sawc_key):
        return {
            "checklist_path":  "",
            "checklist_stats": {
                "skipped":  "sawc_not_found",
                "sawc_key": sawc_key,
                "wall_ms":  int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"sawc {sawc_key!r} not in MinIO — run sawc_write first",
        }
    if not await minio.exists(digest_key):
        return {
            "checklist_path":  "",
            "checklist_stats": {
                "skipped":    "digest_not_found",
                "digest_key": digest_key,
                "wall_ms":    int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"digest {digest_key!r} not in MinIO — run digest_construct first",
        }

    try:
        sawc_text = await minio.read_text(sawc_key)
        sawc = json.loads(sawc_text)
        digest_text = await minio.read_text(digest_key)
        digest = json.loads(digest_text)
    except Exception as e:
        return {
            "checklist_path":  "",
            "checklist_stats": {
                "skipped": "inputs_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"sawc/digest unreadable: {type(e).__name__}: {e}",
        }

    chapter_title = sawc.get("chapter_title") or chapter_id
    sawc_manifest_hash = sawc.get("sawc_manifest_hash") or ""
    digest_manifest_hash = digest.get("digest_manifest_hash") or ""

    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "checklist_eval", "start",
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        n_total_criteria = len(domain.DETERMINISTIC_CHECKS) + len(params.LLM_CRITERIA),
        pass_threshold = 0.80,
    )

    manifest_hash = domain.compute_manifest_hash(
        sawc_manifest_hash = sawc_manifest_hash,
        digest_manifest_hash = digest_manifest_hash,
    )
    versioned_key = keys.versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = keys.latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_total":         cached.get("n_total", 0),
                "n_passed":        cached.get("n_passed", 0),
                "pass_rate":       cached.get("pass_rate", 0.0),
                "chapter_passed":  cached.get("chapter_passed", False),
                "n_failed_feedback": len(cached.get("failed_feedback") or []),
                "failed_feedback":   cached.get("failed_feedback") or [],
                "wall_ms":         elapsed,
                "store_path":      latest_key,
                "versioned_path":  versioned_key,
                "manifest_hash":   manifest_hash,
                "cache_hit":       True,
                "prompt_version":  cached.get("prompt_version"),
                "infra_degraded":  False,   # a cached result is a completed prior run
            }
            await domains.dd.synth.runtime.progress.service.emit_progress(
                thread_id, "checklist_eval", "done",
                n_total = stats["n_total"],
                n_passed = stats["n_passed"],
                pass_rate = stats["pass_rate"],
                chapter_passed = stats["chapter_passed"],
                n_failed_feedback = stats["n_failed_feedback"],
                wall_ms = elapsed, cache_hit = True,
            )
            logger.info(
                f"[checklist_eval] {slug}/{chapter_id}: CACHE HIT — "
                f"{stats['n_passed']}/{stats['n_total']} "
                f"({stats['pass_rate']:.0%}), passed = "
                f"{stats['chapter_passed']}, {elapsed} ms"
            )
            _emit_criterion_scores(slug, cached.get("criteria") or [])
            # Same immediate best-seen promotion as the fresh-compute path
            # below, including the issue #12 tie-breaker — a cache hit is
            # still this iteration's real score. n_pregate_passed isn't a
            # top-level field on the persisted blob, so recount it from
            # the cached criteria list (kind == "deterministic").
            _incoming_best_score = state.get("best_seen_score")
            _incoming_best_pregate = state.get("best_seen_pregate")
            _best_seen_score = _incoming_best_score
            _best_seen_pregate = _incoming_best_pregate
            _best_seen_sawc_path = state.get("best_seen_sawc_path")
            _n_pregate_passed = sum(
                1 for c in (cached.get("criteria") or [])
                if c.get("kind") == "deterministic" and c.get("passed")
            )
            _is_better = (
                _incoming_best_score is None
                or (stats["pass_rate"], _n_pregate_passed) > (
                    _incoming_best_score, _incoming_best_pregate or 0,
                )
            )
            if _is_better:
                _best_seen_score = stats["pass_rate"]
                _best_seen_pregate = _n_pregate_passed
                _best_seen_sawc_path = domains.dd.synth.nodes.sawc.keys.versioned_blob_key(
                    slug, chapter_id, sawc_manifest_hash,
                )
            return {
                "checklist_path": latest_key,
                "checklist_stats": stats,
                # A cache hit is a completed prior run, not a live outage — reset the streak.
                "consecutive_infra_degraded": 0,
                "best_seen_score": _best_seen_score,
                "best_seen_pregate": _best_seen_pregate,
                "best_seen_sawc_path": _best_seen_sawc_path,
            }
        except Exception as e:
            logger.warning(
                f"[checklist_eval] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    pre_results: list[schemas.CriterionResult] = []
    for fn in domain.DETERMINISTIC_CHECKS:
        try:
            pre_results.append(fn(sawc))
        except Exception as e:
            logger.warning(
                f"[checklist_eval] pre-gate {fn.__name__} crashed: "
                f"{type(e).__name__}: {e}"
            )
            pre_results.append(schemas.CriterionResult(
                name = fn.__name__.replace("check_", ""),
                passed = False,
                kind = "deterministic",
                feedback = f"pre_gate_crashed: {type(e).__name__}",
            ))

    pre_failed = [r.name for r in pre_results if not r.passed]
    n_pre_passed = sum(1 for r in pre_results if r.passed)
    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "checklist_eval", "pregates_done",
        n_pregate = len(pre_results),
        n_passed = n_pre_passed,
        names_failed = pre_failed,
    )

    rendered_chapter, truncated = domain.render_chapter_for_judge(sawc)
    rendered_digest = domain.render_digest_for_grounding(digest)

    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "checklist_eval", "judge_request",
        chapter_chars = len(rendered_chapter),
        digest_chars = len(rendered_digest),
        truncated = truncated,
    )

    llm_results, deployment, repaired, judge_wall_ms = await _run_llm_judge(
        thread_id = thread_id,
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        framework = slug,
        sawc = sawc,
        digest = digest,
        rendered_chapter = rendered_chapter,
        rendered_digest = rendered_digest,
        truncated = truncated,
    )

    llm_failed = [r.name for r in llm_results if not r.passed]
    n_llm_passed = sum(1 for r in llm_results if r.passed)
    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "checklist_eval", "judge_done",
        n_llm = len(llm_results),
        n_passed = n_llm_passed,
        names_failed = llm_failed,
        wall_ms = judge_wall_ms,
        deployment = deployment,
        repaired = repaired,
    )

    # CoCoA + atomic-claim grounding run in parallel (no shared state); conservative-bias: never upgrades bundled judge.
    async def _run_faithfulness():
        t0 = time.monotonic()
        try:
            r = await atomic_claim_grounding(
                chapter_prose = rendered_chapter,
                grounding_blob = rendered_digest,
            )
            return r, int((time.monotonic() - t0) * 1000)
        except Exception as e:
            logger.warning(
                f"[checklist_eval] atomic-claim grounding crashed: "
                f"{type(e).__name__}: {e} — skipping augmentation"
            )
            return None, int((time.monotonic() - t0) * 1000)

    async def _run_cocoa():
        t0 = time.monotonic()
        if not params.COCOA_ENABLED:
            # Issue #20 follow-up (2026-09-08): the disable flag lives
            # inside cocoa_alignment_check, but this caller was still
            # doing the full MinIO vault-load BEFORE ever reaching that
            # check — paying (and, twice observed live on ch-01/ch-05
            # retests, sometimes hanging/crashing on) the exact cost the
            # flag was meant to avoid. Skip the load entirely while
            # disabled.
            return None, int((time.monotonic() - t0) * 1000)
        try:
            per_source = digest.get("per_source") or []
            source_keys = sorted({
                s.get("source_key", "") for s in per_source
                if s.get("source_key")
            })
            merged_vault, _, _ = await domains.dd.synth.nodes.render.service._load_per_source_vaults(
                minio, slug, source_keys,
            )
            r = await cocoa_alignment_check(
                sawc_payload = sawc,
                vault = merged_vault,
            )
            return r, int((time.monotonic() - t0) * 1000)
        except Exception as e:
            logger.warning(
                f"[checklist_eval] CoCoA alignment crashed: "
                f"{type(e).__name__}: {e} — skipping augmentation"
            )
            return None, int((time.monotonic() - t0) * 1000)

    (atomic_result, faithfulness_wall_ms), (cocoa_result, cocoa_wall_ms) = (
        await asyncio.gather(_run_faithfulness(), _run_cocoa())
    )

    if atomic_result is not None and not atomic_result["passed"]:
        for i, r in enumerate(llm_results):
            if r.name == "claims_grounded_in_sources":
                llm_results[i] = schemas.CriterionResult(
                    name = r.name,
                    passed = False,
                    kind = r.kind,
                    feedback = atomic_result["feedback"],
                )
                break
        # Recompute the pass counts for telemetry consistency.
        llm_failed = [r.name for r in llm_results if not r.passed]
        n_llm_passed = sum(1 for r in llm_results if r.passed)

    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "checklist_eval", "faithfulness_done",
        method = (atomic_result or {}).get("method", "skipped"),
        resolved = (atomic_result or {}).get("resolved", False),
        n_claims = (atomic_result or {}).get("n_claims", 0),
        n_evaluated = (atomic_result or {}).get("n_evaluated", 0),
        n_unsupported = (atomic_result or {}).get("n_unsupported", 0),
        n_judge_call_failures = (
            (atomic_result or {}).get("n_judge_call_failures", 0)
        ),
        overrode_bundled = (atomic_result is not None
                          and not atomic_result["passed"]),
        wall_ms = faithfulness_wall_ms,
    )

    # CoCoA found drift → override c11+c12 (arXiv 2410.03131).
    if cocoa_result is not None and not cocoa_result["passed"]:
        cocoa_fb = cocoa_result["feedback"]
        for i, r in enumerate(llm_results):
            if r.name in (
                "prose_code_first_not_meta_framing",
                "code_refs_introduced_in_prose",
            ):
                llm_results[i] = schemas.CriterionResult(
                    name = r.name,
                    passed = False,
                    kind = r.kind,
                    feedback = (
                        f"[CoCoA override] {cocoa_fb}"
                        if cocoa_fb else
                        f"[CoCoA override] alignment "
                        f"{cocoa_result['alignment_rate']:.0%} below 85%"
                    ),
                )
        llm_failed = [r.name for r in llm_results if not r.passed]
        n_llm_passed = sum(1 for r in llm_results if r.passed)

    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "checklist_eval", "cocoa_done",
        method = (cocoa_result or {}).get("method", "skipped"),
        resolved = (cocoa_result or {}).get("resolved", False),
        n_pairs = (cocoa_result or {}).get("n_pairs", 0),
        n_judged = (cocoa_result or {}).get("n_judged", 0),
        n_aligned = (cocoa_result or {}).get("n_aligned", 0),
        n_misaligned = (cocoa_result or {}).get("n_misaligned", 0),
        alignment_rate = (cocoa_result or {}).get("alignment_rate", 1.0),
        overrode_bundled = (cocoa_result is not None
                          and not cocoa_result["passed"]),
        wall_ms = cocoa_wall_ms,
    )

    all_results = list(pre_results) + list(llm_results)
    n_passed, n_total, pass_rate, chapter_passed = domain.aggregate_pass_rate(
        all_results
    )
    failed_feedback = domain.collect_failed_feedback(all_results)

    # Best-seen promotion happens HERE, immediately, not one iteration
    # later in sawc_write's own (redundant, one-step-behind) copy of this
    # comparison. Fixed 2026-09-06 — sawc_write only ever compared the
    # PREVIOUS iteration's score at the START of the NEXT call, which
    # never runs if THIS iteration is the one the graph halts on.
    # Confirmed live: iteration 2 scored 57% vs iteration 1's 29%, but the
    # graph halted right after iteration 2's own mgsr_replan — iteration
    # 2's better score never got a chance to be promoted, and the WORSE
    # iteration 1 content shipped as "best-seen" instead. Doing it here
    # means the current iteration's own fresh score is already reflected
    # in state before _route_after_mgsr makes its halt/loop decision.
    # Fixed 2026-09-06 (issue #12) — comparing on pass_rate alone can't
    # tell "genuinely tied quality" apart from "tied only because a judge
    # call failure happened to erase a real structural advantage."
    # Confirmed live: an iteration with the best structural score all
    # chapter (n_pregate_passed) and the only one to write every section
    # tied on overall pass_rate with two earlier, less-complete iterations
    # purely because its OWN bundled judge call failed outright (llm=0/5).
    # A strict pass_rate comparison keeps the earlier (worse) entry on a
    # tie. n_pregate_passed is a deterministic, judge-independent signal
    # (unaffected by a judge call failing that round), so it's the right
    # tie-breaker: prefer more genuinely-verified structural completeness
    # when the headline score doesn't discriminate.
    incoming_best_score = state.get("best_seen_score")
    incoming_best_pregate = state.get("best_seen_pregate")
    best_seen_score = incoming_best_score
    best_seen_pregate = incoming_best_pregate
    best_seen_sawc_path = state.get("best_seen_sawc_path")
    is_better = (
        incoming_best_score is None
        or (pass_rate, n_pre_passed) > (
            incoming_best_score, incoming_best_pregate or 0,
        )
    )
    if is_better:
        best_seen_score = pass_rate
        best_seen_pregate = n_pre_passed
        best_seen_sawc_path = domains.dd.synth.nodes.sawc.keys.versioned_blob_key(
            slug, chapter_id, sawc_manifest_hash,
        )

    evaluation = schemas.ChecklistEvaluation(
        chapter_id = chapter_id,
        chapter_title = chapter_title,
        framework_slug = slug,
        criteria = all_results,
        n_passed = n_passed,
        n_total = n_total,
        pass_rate = pass_rate,
        chapter_passed = chapter_passed,
        failed_feedback = failed_feedback,
        n_llm_judge_repairs = (1 if repaired else 0),
        deployment_judge = deployment,
        wall_ms = int((time.monotonic() - t0) * 1000),
    )
    payload = evaluation.model_dump()
    payload["sawc_manifest_hash"]      = sawc_manifest_hash
    payload["digest_manifest_hash"]    = digest_manifest_hash
    payload["checklist_manifest_hash"] = manifest_hash

    blob_bytes = json.dumps(payload, indent = 2, ensure_ascii = False)
    logger.info(
        f"[checklist_eval] {slug}/{chapter_id}: persisting evaluation "
        f"({len(blob_bytes)} bytes) to MinIO"
    )
    try:
        await asyncio.wait_for(
            minio.write(
                versioned_key, blob_bytes, content_type = "application/json",
            ),
            timeout = params.TIMEOUT_S_PERSIST_WRITE,
        )
        await asyncio.wait_for(
            minio.write(
                latest_key, blob_bytes, content_type = "application/json",
            ),
            timeout = params.TIMEOUT_S_PERSIST_WRITE,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"[checklist_eval] {slug}/{chapter_id}: evaluation persist "
            f"timed out after {params.TIMEOUT_S_PERSIST_WRITE}s (issue #22) — "
            f"MinIO likely degraded; re-raising"
        )
        raise
    logger.info(
        f"[checklist_eval] {slug}/{chapter_id}: evaluation persisted"
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    # A low pass_rate driven by the judge infra itself failing (not by the
    # judge reviewing the chapter and rejecting it) is a different signal
    # than genuine content quality — mgsr's no-recovery short-circuit reads
    # this to decide whether skipping a RETHINK loop is actually justified.
    judge_call_failed = deployment is None
    # Fixed 2026-09-06 — infra_degraded previously only looked at
    # checklist's OWN call health (judge/cocoa/atomic-claim), completely
    # missing sawc_write's independently-timing-out writer calls. Confirmed
    # live across 3 consecutive chapters the same day: a chapter whose
    # writer failed every draft attempt with APITimeoutError still read
    # infra_degraded=False whenever checklist's own judge happened to get
    # a response that round — causing a chapter run to burn the full
    # 5-iteration budget (~33 min) instead of halting early (~16 min), and
    # separately causing genuine infra-driven total failures to skip
    # straight to HALT no-recovery with zero RETHINK attempts.
    # "parse_failed"/"pydantic_fail" are excluded — those are genuine
    # model-output-quality issues, not infra; everything else in
    # sawc_stats.error_breakdown is by construction an exception class
    # name from a failed LLM call (timeout, rate limit, context overflow,
    # server error, ...).
    sawc_error_breakdown = (state.get("sawc_stats") or {}).get("error_breakdown") or {}
    sawc_writer_degraded = any(
        kind not in ("parse_failed", "pydantic_fail")
        for kind in sawc_error_breakdown
    )
    infra_degraded = (
        judge_call_failed
        or sawc_writer_degraded
        or (atomic_result is not None and atomic_result.get("resolved") is False)
        or (cocoa_result is not None and cocoa_result.get("resolved") is False)
    )
    # Sustained-outage streak: how many RETHINK iterations IN A ROW,
    # ending with this one, were infra-degraded. Resets to 0 the moment an
    # iteration genuinely gets judged (even if it fails on content quality).
    # graph._route_after_mgsr halts early once this crosses a threshold,
    # instead of burning the full refine budget against an outage that
    # isn't clearing (confirmed live: 3+ consecutive degraded iterations
    # produced zero improvement before this counter existed).
    consecutive_infra_degraded = (
        int(state.get("consecutive_infra_degraded") or 0) + 1
        if infra_degraded else 0
    )
    stats = {
        "n_total":            n_total,
        "n_passed":           n_passed,
        "pass_rate":          pass_rate,
        "chapter_passed":     chapter_passed,
        "n_failed_feedback":  len(failed_feedback),
        # The actual strings, not just the count — sawc_write's RETHINK
        # iteration reads this to close the self-refine loop (previously
        # a RETHINK reran blind with zero signal about what was wrong).
        "failed_feedback":    failed_feedback,
        "n_pregate_passed":   n_pre_passed,
        "n_pregate_total":    len(pre_results),
        "n_llm_passed":       n_llm_passed,
        "n_llm_total":        len(llm_results),
        "names_failed":       [r.name for r in all_results if not r.passed],
        "judge_wall_ms":      judge_wall_ms,
        "judge_repaired":     repaired,
        "judge_call_failed":  judge_call_failed,
        "infra_degraded":     infra_degraded,
        "sawc_writer_degraded": sawc_writer_degraded,
        "wall_ms":            elapsed,
        "store_path":         latest_key,
        "versioned_path":     versioned_key,
        "manifest_hash":      manifest_hash,
        "cache_hit":          False,
        "prompt_version":     versions.CHECKLIST_PROMPT_VERSION,
        "deployment_judge":   deployment,
    }
    await domains.dd.synth.runtime.progress.service.emit_progress(
        thread_id, "checklist_eval", "done",
        n_total = n_total,
        n_passed = n_passed,
        pass_rate = pass_rate,
        chapter_passed = chapter_passed,
        n_failed_feedback = len(failed_feedback),
        wall_ms = elapsed,
    )
    logger.info(
        f"[checklist_eval] {slug}/{chapter_id}: "
        f"{n_passed}/{n_total} criteria passed "
        f"({pass_rate:.0%}, threshold 80%, chapter_passed = {chapter_passed}); "
        f"pre = {n_pre_passed}/{len(pre_results)}, llm = {n_llm_passed}/{len(llm_results)}; "
        f"{len(failed_feedback)} feedback strings; "
        f"judge_wall = {judge_wall_ms}ms, total = {elapsed}ms"
    )
    _emit_criterion_scores(slug, all_results)
    return {
        "checklist_path": latest_key,
        "checklist_stats": stats,
        # Top-level (not nested in checklist_stats) — graph._route_after_mgsr
        # reads SynthState fields directly, same as refine_iter/best_seen_score.
        "consecutive_infra_degraded": consecutive_infra_degraded,
        "best_seen_score": best_seen_score,
        "best_seen_pregate": best_seen_pregate,
        "best_seen_sawc_path": best_seen_sawc_path,
    }
