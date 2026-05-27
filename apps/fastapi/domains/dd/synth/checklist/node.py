"""checklist_eval — Binary checklist evaluator (RefineBench + CheckEval).

Step 7 of the synth pipeline (per
`docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md` + the checklist_eval deep
research report). The fourth LLM-driven synth graph node, runs after
sawc_write commits its checkpoint.

WHAT IT DOES (per chapter):

  1. Loads sawc-latest.json (from sawc_write) + digest-latest.json
     (from digest_construct, for grounding in the LLM-judge prompt).
  2. Runs 7 DETERMINISTIC pre-gates over sawc's `coverage_stats` +
     `sections`. Pure-Python, <10ms total, near-zero cost.
  3. Renders the chapter as a markdown-ish block + the digest as a
     compressed per-section grounding block.
  4. Fires 1 batched LLM-judge call returning 5 binary verdicts as a
     single JSON object. Pydantic-validated; one repair pass on
     malformed JSON / missing keys.
  5. Aggregates 12 CriterionResults into pass_rate + chapter_passed
     (threshold 0.80). Extracts failed_feedback strings for the
     downstream mgsr_replan node.
  6. Persists ChecklistEvaluation to MinIO (versioned + latest pointer).
  7. Returns state patch with `checklist_path` + `checklist_stats`.

CACHING — content-addressed:

  versioned: synth/{slug}/{chapter_id}/checklist/{manifest_hash}.json
  latest:    synth/{slug}/{chapter_id}/checklist-latest.json

  Manifest hash includes:
    sawc_manifest_hash
    digest_manifest_hash
    prompt_version
    schema_version

  Cache hit → emit `done` SSE with cache_hit=true, return immediately.

FAIL-SOFT BEHAVIOR:

  - LLM-judge call fails (HTTP error): emit 5 LLM verdicts as FAILED
    with `feedback="judge_unavailable"` so the chapter conservatively
    "fails the LLM layer" (deterministic pre-gates still scored
    correctly). Lets the pipeline continue; mgsr_replan sees a low
    pass_rate and triggers another iteration.
  - LLM-judge returns malformed JSON: one repair attempt with the
    parse error as feedback. If repair also fails, fall back to the
    judge_unavailable behavior above.
  - Pydantic validation fails on the LLM response (wrong keys, missing
    fields): repair attempt with structural issues spelled out.

SSE EVENTS — real-time UI mechanism (per the established pattern):

  start            chapter_id, chapter_title, n_total_criteria=12,
                    pass_threshold=0.80
  pregates_done    n_pregate=7, n_passed, names_failed[]
  judge_request    wall_ms_so_far, deployment? (when bandit picks)
  judge_done       n_llm=5, n_passed, names_failed[], wall_ms, deployment,
                    repaired? (bool — did we run a Pydantic-repair pass?)
  done             n_total=12, n_passed, pass_rate, chapter_passed,
                    n_failed_feedback, wall_ms, cache_hit
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from hashlib import sha256
from typing import Optional
from pydantic import ValidationError

from ...ingestion.storage import get_storage
from domains.llm.rotator.chain import chat_judge_bandit_async

from .constants import (
    CHECKLIST_PROMPT_VERSION,
    CHECKLIST_SCHEMA_VERSION,
)
from .cocoa import cocoa_alignment_check
from .faithfulness import atomic_claim_grounding
from .types import (
    ChecklistEvaluation,
    CriterionResult,
    _LLMJudgePayload,
)
from .constants import _LLM_CRITERIA
from .service import (
    DETERMINISTIC_CHECKS,
    aggregate_pass_rate,
    build_judge_prompt,
    build_repair_prompt,
    collect_failed_feedback,
    llm_payload_to_criteria,
    render_chapter_for_judge,
    render_digest_for_grounding,
)
from ..observability.spans import traced
from ..progress import emit_progress
from ..state import SynthState


logger = logging.getLogger(__name__)


# =============================================================================
# Tunables
# =============================================================================
_TEMPERATURE_JUDGE      = 0.0
_TEMPERATURE_REPAIR     = 0.0
_MAX_TOKENS_JUDGE       = 3000
_MAX_TOKENS_REPAIR      = 3000
_MAX_REPAIR_ATTEMPTS    = 1

_BLOB_PREFIX = "synth"
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# DD-SYNTH-SPEED-SOTA #A3 (2026-05-26) — structured-output schema for the
# batched 5-criterion LLM judge + repair calls. NIM + Mistral honor
# response_format=json_schema server-side; repair loop still handles slips.
_JUDGE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "checklist_judge",
        "schema": _LLMJudgePayload.model_json_schema(),
        "strict": False,
    },
}


# =============================================================================
# Blob keys
# =============================================================================
def _versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/checklist/{manifest_hash}.json"


def _latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/checklist-latest.json"


def _sawc_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/sawc-latest.json"


def _digest_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/digest-latest.json"


# =============================================================================
# JSON helpers
# =============================================================================
def _parse_json_response(text: str) -> Optional[dict]:
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
    m = _JSON_RE.search(text)
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


def _try_parse_judge(
    raw: dict,
) -> tuple[Optional[_LLMJudgePayload], Optional[str]]:
    try:
        return _LLMJudgePayload.model_validate(raw), None
    except ValidationError as e:
        return None, _shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


# =============================================================================
# Fallback verdicts when LLM-judge is unavailable
# =============================================================================
def _fallback_llm_verdicts(reason: str) -> list[CriterionResult]:
    """When the judge LLM is unreachable / malformed beyond repair,
    conservatively mark all 5 LLM criteria as failed with the reason
    as feedback. This drops chapter pass_rate to at most 7/12 = 58%
    (below the 80% threshold), so mgsr_replan will be invoked — which
    is the correct behavior when we can't verify the chapter."""
    out: list[CriterionResult] = []
    for name in _LLM_CRITERIA:
        out.append(CriterionResult(
            name=name,
            passed=False,
            kind="llm_judge",
            feedback=(
                f"judge_unavailable: {reason}. Conservatively marked "
                f"FAIL so mgsr_replan re-evaluates next iteration."
            ),
        ))
    return out


# =============================================================================
# LLM-judge pipeline
# =============================================================================
async def _run_llm_judge(
    *,
    thread_id: str,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    rendered_chapter: str,
    rendered_digest: str,
    truncated: bool,
) -> tuple[list[CriterionResult], Optional[str], bool, int]:
    """Fire the batched LLM-judge call → parse → validate → repair-if-needed.

    Returns (criteria_results, deployment, was_repaired, wall_ms).
    On hard failure, returns a fallback set of FAILED verdicts so the
    chapter conservatively fails the LLM layer.
    """
    t0 = time.monotonic()
    prompt = build_judge_prompt(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        framework=framework,
        rendered_chapter=rendered_chapter,
        rendered_digest=rendered_digest,
        truncated=truncated,
    )

    deployment: Optional[str] = None
    try:
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_JUDGE,
            temperature=_TEMPERATURE_JUDGE,
            response_format=_JUDGE_RESPONSE_FORMAT,
        )
        deployment = (meta or {}).get("deployment")
    except Exception as e:
        wall_ms = int((time.monotonic() - t0) * 1000)
        logger.warning(
            f"[checklist_eval] LLM judge call failed: "
            f"{type(e).__name__}: {e}"
        )
        return (
            _fallback_llm_verdicts(f"{type(e).__name__}"),
            None, False, wall_ms,
        )

    parsed = _parse_json_response(response)
    payload: Optional[_LLMJudgePayload] = None
    err: Optional[str] = None
    repaired = False

    if parsed is not None:
        payload, err = _try_parse_judge(parsed)

    # One repair attempt if parse OR Pydantic failed
    if payload is None and _MAX_REPAIR_ATTEMPTS > 0:
        repair_issues = [
            err if err else "previous response was not parseable JSON"
        ]
        current_json = json.dumps(parsed or {"_raw": (response or "")[:400]})
        repair_prompt = build_repair_prompt(
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            framework=framework,
            rendered_chapter=rendered_chapter,
            rendered_digest=rendered_digest,
            truncated=truncated,
            current_json=current_json,
            issues=repair_issues,
        )
        try:
            rr, rm = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
                response_format=_JUDGE_RESPONSE_FORMAT,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = _parse_json_response(rr)
            if rp is not None:
                payload, err = _try_parse_judge(rp)
                if payload is not None:
                    repaired = True
        except Exception as e:
            logger.warning(
                f"[checklist_eval] LLM judge repair failed: "
                f"{type(e).__name__}: {e}"
            )

    wall_ms = int((time.monotonic() - t0) * 1000)

    if payload is None:
        logger.warning(
            f"[checklist_eval] LLM judge unparseable after repair "
            f"({err}); using fallback FAIL verdicts"
        )
        return (
            _fallback_llm_verdicts(f"judge_parse_failed: {err}"),
            deployment, False, wall_ms,
        )

    return llm_payload_to_criteria(payload), deployment, repaired, wall_ms


# =============================================================================
# Manifest hash
# =============================================================================
def _compute_manifest_hash(
    *,
    sawc_manifest_hash: str,
    digest_manifest_hash: str,
) -> str:
    payload = (
        f"sawc={sawc_manifest_hash}|"
        f"digest={digest_manifest_hash}|"
        f"prompt={CHECKLIST_PROMPT_VERSION}|"
        f"schema={CHECKLIST_SCHEMA_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# The node
# =============================================================================
@traced("checklist_eval")
async def checklist_eval(state: SynthState) -> dict:
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
    minio = get_storage()

    # -- Load sawc + digest blobs -------------------------------------------
    sawc_key = _sawc_latest_key(slug, chapter_id)
    digest_key = _digest_latest_key(slug, chapter_id)

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

    await emit_progress(
        thread_id, "checklist_eval", "start",
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        n_total_criteria=len(DETERMINISTIC_CHECKS) + len(_LLM_CRITERIA),
        pass_threshold=0.80,
    )

    # -- Cache fast-path ----------------------------------------------------
    manifest_hash = _compute_manifest_hash(
        sawc_manifest_hash=sawc_manifest_hash,
        digest_manifest_hash=digest_manifest_hash,
    )
    versioned_key = _versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = _latest_blob_key(slug, chapter_id)

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
                "wall_ms":         elapsed,
                "store_path":      latest_key,
                "versioned_path":  versioned_key,
                "manifest_hash":   manifest_hash,
                "cache_hit":       True,
                "prompt_version":  cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "checklist_eval", "done",
                n_total=stats["n_total"],
                n_passed=stats["n_passed"],
                pass_rate=stats["pass_rate"],
                chapter_passed=stats["chapter_passed"],
                n_failed_feedback=stats["n_failed_feedback"],
                wall_ms=elapsed, cache_hit=True,
            )
            logger.info(
                f"[checklist_eval] {slug}/{chapter_id}: CACHE HIT — "
                f"{stats['n_passed']}/{stats['n_total']} "
                f"({stats['pass_rate']:.0%}), passed="
                f"{stats['chapter_passed']}, {elapsed} ms"
            )
            return {"checklist_path": latest_key, "checklist_stats": stats}
        except Exception as e:
            logger.warning(
                f"[checklist_eval] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    # -- Layer 1: 7 deterministic pre-gates ---------------------------------
    pre_results: list[CriterionResult] = []
    for fn in DETERMINISTIC_CHECKS:
        try:
            pre_results.append(fn(sawc))
        except Exception as e:
            # Defensive: a check shouldn't crash, but if it does we
            # surface a clear FAIL so the operator can see which one
            logger.warning(
                f"[checklist_eval] pre-gate {fn.__name__} crashed: "
                f"{type(e).__name__}: {e}"
            )
            pre_results.append(CriterionResult(
                name=fn.__name__.replace("check_", ""),
                passed=False,
                kind="deterministic",
                feedback=f"pre_gate_crashed: {type(e).__name__}",
            ))

    pre_failed = [r.name for r in pre_results if not r.passed]
    n_pre_passed = sum(1 for r in pre_results if r.passed)
    await emit_progress(
        thread_id, "checklist_eval", "pregates_done",
        n_pregate=len(pre_results),
        n_passed=n_pre_passed,
        names_failed=pre_failed,
    )

    # -- Render chapter + digest for the LLM-judge prompt -------------------
    rendered_chapter, truncated = render_chapter_for_judge(sawc)
    rendered_digest = render_digest_for_grounding(digest)

    await emit_progress(
        thread_id, "checklist_eval", "judge_request",
        chapter_chars=len(rendered_chapter),
        digest_chars=len(rendered_digest),
        truncated=truncated,
    )

    # -- Layer 2: 1 batched LLM-judge call ----------------------------------
    llm_results, deployment, repaired, judge_wall_ms = await _run_llm_judge(
        thread_id=thread_id,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        framework=slug,
        rendered_chapter=rendered_chapter,
        rendered_digest=rendered_digest,
        truncated=truncated,
    )

    llm_failed = [r.name for r in llm_results if not r.passed]
    n_llm_passed = sum(1 for r in llm_results if r.passed)
    await emit_progress(
        thread_id, "checklist_eval", "judge_done",
        n_llm=len(llm_results),
        n_passed=n_llm_passed,
        names_failed=llm_failed,
        wall_ms=judge_wall_ms,
        deployment=deployment,
        repaired=repaired,
    )

    # -- Augment: atomic-claim grounding check (2026-05-24) -----------------
    # The bundled judge above gives a coarse PASS/FAIL on
    # `claims_grounded_in_sources` based on a 3-5 citation spot-check. This
    # separate pass extracts atomic claims + verifies each against the digest
    # grounding via bandit-routed LLM calls (per-claim, parallel concurrency=8).
    # If atomic check finds any unsupported claim, we OVERRIDE the bundled
    # judge's verdict to FAIL with specific feedback. Conservative bias:
    # never upgrades the bundled judge — only downgrades it.
    # See docs/KD-SYNTH-SOTA-2026-05-24.md §3 #2.
    # DD-SYNTH-SPEED-SOTA #B1 (2026-05-26) — Parallelize CoCoA + atomic-
    # claim grounding. Both run on the same chapter draft; they share NO
    # state (atomic uses prose+digest; CoCoA uses sawc+vault). Running
    # them concurrently via asyncio.gather drops the ~3-5 min serial path
    # to ~max(2.5, 3.5) min ≈ 30-40% on the checklist tail. Each task is
    # wrapped in its own try/except so the fail-soft semantics are
    # preserved per-result.
    async def _run_faithfulness():
        t0 = time.monotonic()
        try:
            r = await atomic_claim_grounding(
                chapter_prose=rendered_chapter,
                grounding_blob=rendered_digest,
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
        try:
            from ..render.node import _load_per_source_vaults as _load_vault
            per_source = digest.get("per_source") or []
            source_keys = sorted({
                s.get("source_key", "") for s in per_source
                if s.get("source_key")
            })
            merged_vault, _, _ = await _load_vault(minio, slug, source_keys)
            r = await cocoa_alignment_check(
                sawc_payload=sawc,
                vault=merged_vault,
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
        # Override the bundled judge's `claims_grounded_in_sources` verdict.
        # Find the entry by name and rebuild it as a failure with the
        # atomic-claim feedback. CriterionResult shape is preserved.
        for i, r in enumerate(llm_results):
            if r.name == "claims_grounded_in_sources":
                llm_results[i] = CriterionResult(
                    name=r.name,
                    passed=False,
                    kind=r.kind,
                    feedback=atomic_result["feedback"],
                )
                break
        # Recompute the pass counts for telemetry consistency.
        llm_failed = [r.name for r in llm_results if not r.passed]
        n_llm_passed = sum(1 for r in llm_results if r.passed)

    await emit_progress(
        thread_id, "checklist_eval", "faithfulness_done",
        method=(atomic_result or {}).get("method", "skipped"),
        n_claims=(atomic_result or {}).get("n_claims", 0),
        n_unsupported=(atomic_result or {}).get("n_unsupported", 0),
        overrode_bundled=(atomic_result is not None
                          and not atomic_result["passed"]),
        wall_ms=faithfulness_wall_ms,
    )

    # CoCoA two-stage code/explanation alignment override path. Augments
    # the bundled judge's c11/c12 verdicts when drift is detected. Note:
    # the cocoa_result + cocoa_wall_ms were computed above in parallel
    # with the atomic-claim check via _run_cocoa(). See arXiv 2410.03131.
    if cocoa_result is not None and not cocoa_result["passed"]:
        # CoCoA found drift — override c11 + c12. Each gets the same
        # alignment-rate-grounded feedback so mgsr_replan sees specific
        # misaligned-subtopic samples and routes the reroll surgically.
        cocoa_fb = cocoa_result["feedback"]
        for i, r in enumerate(llm_results):
            if r.name in (
                "prose_code_first_not_meta_framing",
                "code_refs_introduced_in_prose",
            ):
                llm_results[i] = CriterionResult(
                    name=r.name,
                    passed=False,
                    kind=r.kind,
                    feedback=(
                        f"[CoCoA override] {cocoa_fb}"
                        if cocoa_fb else
                        f"[CoCoA override] alignment "
                        f"{cocoa_result['alignment_rate']:.0%} below 85%"
                    ),
                )
        llm_failed = [r.name for r in llm_results if not r.passed]
        n_llm_passed = sum(1 for r in llm_results if r.passed)

    await emit_progress(
        thread_id, "checklist_eval", "cocoa_done",
        method=(cocoa_result or {}).get("method", "skipped"),
        n_pairs=(cocoa_result or {}).get("n_pairs", 0),
        n_aligned=(cocoa_result or {}).get("n_aligned", 0),
        n_misaligned=(cocoa_result or {}).get("n_misaligned", 0),
        alignment_rate=(cocoa_result or {}).get("alignment_rate", 1.0),
        overrode_bundled=(cocoa_result is not None
                          and not cocoa_result["passed"]),
        wall_ms=cocoa_wall_ms,
    )

    # -- Aggregate ----------------------------------------------------------
    all_results = list(pre_results) + list(llm_results)
    n_passed, n_total, pass_rate, chapter_passed = aggregate_pass_rate(
        all_results
    )
    failed_feedback = collect_failed_feedback(all_results)

    # -- Persist ------------------------------------------------------------
    evaluation = ChecklistEvaluation(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        framework_slug=slug,
        criteria=all_results,
        n_passed=n_passed,
        n_total=n_total,
        pass_rate=pass_rate,
        chapter_passed=chapter_passed,
        failed_feedback=failed_feedback,
        n_llm_judge_repairs=(1 if repaired else 0),
        deployment_judge=deployment,
        wall_ms=int((time.monotonic() - t0) * 1000),
    )
    payload = evaluation.model_dump()
    payload["sawc_manifest_hash"]      = sawc_manifest_hash
    payload["digest_manifest_hash"]    = digest_manifest_hash
    payload["checklist_manifest_hash"] = manifest_hash

    blob_bytes = json.dumps(payload, indent=2, ensure_ascii=False)
    await minio.write(
        versioned_key, blob_bytes, content_type="application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type="application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_total":            n_total,
        "n_passed":           n_passed,
        "pass_rate":          pass_rate,
        "chapter_passed":     chapter_passed,
        "n_failed_feedback":  len(failed_feedback),
        "n_pregate_passed":   n_pre_passed,
        "n_pregate_total":    len(pre_results),
        "n_llm_passed":       n_llm_passed,
        "n_llm_total":        len(llm_results),
        "names_failed":       [r.name for r in all_results if not r.passed],
        "judge_wall_ms":      judge_wall_ms,
        "judge_repaired":     repaired,
        "wall_ms":            elapsed,
        "store_path":         latest_key,
        "versioned_path":     versioned_key,
        "manifest_hash":      manifest_hash,
        "cache_hit":          False,
        "prompt_version":     CHECKLIST_PROMPT_VERSION,
        "deployment_judge":   deployment,
    }
    await emit_progress(
        thread_id, "checklist_eval", "done",
        n_total=n_total,
        n_passed=n_passed,
        pass_rate=pass_rate,
        chapter_passed=chapter_passed,
        n_failed_feedback=len(failed_feedback),
        wall_ms=elapsed,
    )
    logger.info(
        f"[checklist_eval] {slug}/{chapter_id}: "
        f"{n_passed}/{n_total} criteria passed "
        f"({pass_rate:.0%}, threshold 80%, chapter_passed={chapter_passed}); "
        f"pre={n_pre_passed}/{len(pre_results)}, llm={n_llm_passed}/{len(llm_results)}; "
        f"{len(failed_feedback)} feedback strings; "
        f"judge_wall={judge_wall_ms}ms, total={elapsed}ms"
    )
    return {"checklist_path": latest_key, "checklist_stats": stats}


# =============================================================================
# Convenience loader for downstream nodes
# =============================================================================
def load_checklist_payload(text: str) -> dict:
    """Parse the persisted checklist blob. Downstream (mgsr_replan)
    consumes `failed_feedback` + per-criterion `feedback` for guided
    refinement instructions."""
    return json.loads(text)
