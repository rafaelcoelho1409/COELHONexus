"""digest_construct — LLM-assigned source-to-section routing.

Step 4 of the synth pipeline (per
`docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md` + the digest_construct
deep research report). The second LLM-driven synth graph node, runs
after outline_sdp commits its checkpoint.

WHAT IT DOES (per chapter):

  1. Loads the outline blob produced by outline_sdp from
     `synth/{slug}/{chapter_id}/outline-latest.json`. The blob carries
     both the outline sections AND the source_keys, so this node has
     everything it needs in one read.
  2. Reads each source page from MinIO (normalized + sentinelized).
  3. For each source, extracts the vault hashes present (so the LLM
     only routes hashes it ACTUALLY sees, not hallucinated ones).
  4. Fires ONE LLM call PER SOURCE in parallel (capped at
     `_CONCURRENCY` via `asyncio.Semaphore` — matches planner pattern).
     Emits a `source_done` SSE event AS each source completes (real-
     time UI progress through the long parallel-fan-out phase).
  5. Parses + Pydantic-validates each digest. Cross-references the
     LLM output against the outline's section_ids + the source's
     actual vault hashes; on issues, runs a repair LLM call.
  6. Aggregates: builds `per_section` index (inverse of per_source),
     computes `CoverageStats` (empty sections, over-spread sources,
     orphan code_refs, fan-out metrics).
  7. Persists the full `ChapterDigest` to MinIO (versioned + latest
     pointer, content-addressed by manifest hash).
  8. Returns state patch with `digest_path` + `digest_stats`.

CACHING — content-addressed:

  versioned: synth/{slug}/{chapter_id}/digest/{manifest_hash}.json
  latest:    synth/{slug}/{chapter_id}/digest-latest.json

  Manifest hash includes:
    outline_manifest_hash  (the outline this digest is keyed to)
    sources_sha            (sorted source MinIO keys)
    sources_bytes          (post-concat byte count — invalidates on
                            ingestion changes)
    n_sources
    prompt_version
    schema_version

  Cache hit returns immediately + emits `done` SSE with cache_hit=true.

FAIL-SOFT BEHAVIOR (matches outline_sdp's pattern):

  - One source's LLM call fails: log + emit `sample_done(ok=false)`,
    skip that source's digest, continue. Empty contributions for that
    source surface in the aggregate as missing coverage; mgsr_replan
    can request a retry.
  - Pydantic validation fails on a source's digest: run one repair
    LLM call with `validate_source_digest` issues as the feedback. If
    repair also fails, drop that source's digest (same as above).
  - All N sources fail: emit a minimal ChapterDigest with empty
    per_source + empty per_section (no contributions). mgsr_replan
    surfaces the coverage_stats.empty_sections = ALL list and can
    request a full retry.

SSE EVENTS (per the established pattern in outline_sdp):

  start              — chapter_id, chapter_title, n_sections, n_sources
  outline_loaded     — n_sections, n_sources, n_total_vault_hashes
  source_done        — source_idx, n_total, ok, source_key,
                        n_contributions, wall_ms, deployment, error?
  digests_aggregated — n_digests_ok, n_pydantic_fail, n_total
  done               — n_sources, n_sections_covered, n_empty_sections,
                        n_orphan_code_refs, wall_ms, cache_hit

OBSERVABILITY:

  Each `source_done` event includes the deployment ID picked by the
  ParetoBandit so we can see fan-out across providers in real-time.
  The `dd-grader` bandit cells share state with outline_sdp's cells —
  successful digest calls reinforce the same deployments that worked
  for outline drafting.
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
    DIGEST_PROMPT_VERSION,
    DIGEST_SCHEMA_VERSION,
)
from .service import (
    build_digest_prompt,
    build_per_section_index,
    build_repair_prompt,
    compute_coverage_stats,
    derive_source_title_fallback,
    extract_vault_hashes,
    merge_overlapping_sections,
    validate_source_digest,
)
from .types import (
    ChapterDigest,
    CoverageStats,
    SourceDigest,
    _LLMDigestPayload,
)
from ..observability.spans import traced
from ..progress import emit_progress
from ..state import SynthState


logger = logging.getLogger(__name__)


# =============================================================================
# Tunables (quality > speed)
# =============================================================================
_CONCURRENCY        = 24    # max concurrent per-source LLM calls
# V4 (2026-05-28) — bumped 16 → 24 after per-arm cooldown (60s) and
# bandit drift control stabilized. Empirical evidence: 2026-05-27 run
# with concurrency 16 saw no 429 cascades for digest (cooldown caught
# every per-arm spike). 24 is conservative — expected per-chapter
# digest wall-time drop of ~20-25% on 60+ source chapters.
# Bumped 2026-05-25 from 6 → 16. Digest is the single heaviest synth
# step (~17 min for FastMCP's 252 sources at N=6) because it fans out
# one LLM call per ingested page. At ~4s per page, wall time ≈
# n_sources × per_call_s / N. The FGTS-VA bandit distributes across
# ~30 free-tier providers (NIM, Mistral, Gemini, Cerebras, Groq, etc.)
# so per-provider 429s under N=16 are absorbed by the bandit's
# existing cooldown + arm rotation — no per-call code change needed.
# Expected: 252 × 4s / 16 ≈ 65s ideal; realistic ≈ 7-9 min after
# accounting for repair attempts, larger-page outliers, and bandit
# cooldown headroom. Quality is byte-identical (same prompts, same
# model pool); this is pure throughput.
_TEMPERATURE_DRAFT  = 0.1   # routing decisions should be ~deterministic
_TEMPERATURE_REPAIR = 0.0
_MAX_TOKENS_DRAFT   = 6000
_MAX_TOKENS_REPAIR  = 6000
_MAX_REPAIR_ATTEMPTS = 2

# Per-source body cap — generous since each LLM sees ONLY one source.
# Most pages are <30K chars; cap at 100K to be safe.
_MAX_SOURCE_CHARS = 100_000

_BLOB_PREFIX = "synth"
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# DD-SYNTH-SPEED-SOTA #A1 (2026-05-26) — structured-output schema for the
# per-source digest call. NIM + Mistral honor response_format=json_schema
# server-side. Caller's existing Pydantic repair loop catches anything
# that slips through (e.g. Gemini, where translation is rough).
_DIGEST_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "source_digest",
        "schema": _LLMDigestPayload.model_json_schema(),
        "strict": False,
    },
}


# =============================================================================
# Blob keys
# =============================================================================
def _versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return (
        f"{_BLOB_PREFIX}/{slug}/{chapter_id}/digest/{manifest_hash}.json"
    )


def _latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/digest-latest.json"


def _outline_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/outline-latest.json"


# =============================================================================
# JSON helpers
# =============================================================================
def _parse_json_response(text: str) -> Optional[dict]:
    """Best-effort JSON extraction. Tolerates ```json fences + leading
    prose. Same approach as outline_sdp / planner.chapter_select."""
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
    for err in errs[:4]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}")
    suffix = f" (+{len(errs) - 4} more)" if len(errs) > 4 else ""
    return "; ".join(lines) + suffix


def _try_parse_payload(
    raw: dict,
) -> tuple[Optional[_LLMDigestPayload], Optional[str]]:
    try:
        return _LLMDigestPayload.model_validate(raw), None
    except ValidationError as e:
        return None, _shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


# =============================================================================
# Per-source pipeline
# =============================================================================
async def _digest_one_source(
    *,
    sem: asyncio.Semaphore,
    sample_idx: int,
    n_total: int,
    thread_id: str,
    chapter_id: str,
    chapter_title: str,
    framework: str,
    outline_sections: list[dict],
    valid_section_ids: set[str],
    source_key: str,
    source_md: str,
) -> Optional[SourceDigest]:
    """One source's full digest lifecycle: prompt → LLM → parse →
    validate → repair-if-needed → SourceDigest. Returns None on
    irrecoverable failure.

    Emits one `source_done` event per source (real-time progress
    through the fan-out)."""
    async with sem:
        t0 = time.monotonic()
        source_vault_hashes = extract_vault_hashes(source_md)
        valid_hash_set = set(source_vault_hashes)

        prompt = build_digest_prompt(
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            framework=framework,
            outline_sections=outline_sections,
            source_key=source_key,
            source_md=source_md[:_MAX_SOURCE_CHARS],
            source_vault_hashes=source_vault_hashes,
        )

        deployment: Optional[str] = None
        try:
            response, meta = await chat_judge_bandit_async(
                prompt,
                max_tokens=_MAX_TOKENS_DRAFT,
                temperature=_TEMPERATURE_DRAFT,
                response_format=_DIGEST_RESPONSE_FORMAT,
            )
            deployment = (meta or {}).get("deployment")
        except Exception as e:
            wall_ms = int((time.monotonic() - t0) * 1000)
            await emit_progress(
                thread_id, "digest_construct", "source_done",
                sample_idx=sample_idx, n_total=n_total,
                source_key=source_key, ok=False,
                error=f"{type(e).__name__}: {str(e)[:120]}",
                wall_ms=wall_ms,
            )
            logger.warning(
                f"[digest_construct] {source_key}: LLM call failed: "
                f"{type(e).__name__}: {e}"
            )
            return None

        parsed = _parse_json_response(response)
        if not parsed:
            wall_ms = int((time.monotonic() - t0) * 1000)
            await emit_progress(
                thread_id, "digest_construct", "source_done",
                sample_idx=sample_idx, n_total=n_total,
                source_key=source_key, ok=False,
                error="parse_failed", wall_ms=wall_ms,
                deployment=deployment,
            )
            logger.info(
                f"[digest_construct] {source_key}: response not parseable as JSON"
            )
            return None

        payload, err = _try_parse_payload(parsed)
        if payload is None:
            # Pydantic rejected — try ONE repair pass before giving up
            attempt = 0
            current = parsed
            while attempt < _MAX_REPAIR_ATTEMPTS and payload is None:
                attempt += 1
                # The "issues" feedback for a Pydantic failure is the
                # error string; for content-level failures we'll use
                # the richer validate_source_digest output below
                issues = [f"Pydantic schema rejected the previous output: {err}"]
                repair_prompt = build_repair_prompt(
                    chapter_id=chapter_id,
                    chapter_title=chapter_title,
                    framework=framework,
                    outline_sections=outline_sections,
                    source_key=source_key,
                    source_md=source_md[:_MAX_SOURCE_CHARS],
                    source_vault_hashes=source_vault_hashes,
                    current_json=json.dumps(current, indent=2),
                    issues=issues,
                )
                try:
                    rr, rm = await chat_judge_bandit_async(
                        repair_prompt,
                        max_tokens=_MAX_TOKENS_REPAIR,
                        temperature=_TEMPERATURE_REPAIR,
                        response_format=_DIGEST_RESPONSE_FORMAT,
                    )
                    deployment = (rm or {}).get("deployment") or deployment
                    rp = _parse_json_response(rr)
                    if rp:
                        current = rp
                        payload, err = _try_parse_payload(rp)
                except Exception as e:
                    logger.warning(
                        f"[digest_construct] {source_key}: repair "
                        f"attempt {attempt} failed: "
                        f"{type(e).__name__}: {e}"
                    )
                    break

            if payload is None:
                wall_ms = int((time.monotonic() - t0) * 1000)
                await emit_progress(
                    thread_id, "digest_construct", "source_done",
                    sample_idx=sample_idx, n_total=n_total,
                    source_key=source_key, ok=False,
                    error=f"pydantic_fail: {err}", wall_ms=wall_ms,
                    deployment=deployment,
                )
                logger.info(
                    f"[digest_construct] {source_key}: pydantic-reject "
                    f"after {_MAX_REPAIR_ATTEMPTS} repairs: {err}"
                )
                return None

        # Content-level cross-reference validation
        issues = validate_source_digest(
            payload,
            valid_section_ids=valid_section_ids,
            valid_vault_hashes=valid_hash_set,
        )
        if issues:
            # Run a content-repair pass with the actionable issues
            attempt = 0
            current = payload.model_dump()
            while attempt < _MAX_REPAIR_ATTEMPTS and issues:
                attempt += 1
                repair_prompt = build_repair_prompt(
                    chapter_id=chapter_id,
                    chapter_title=chapter_title,
                    framework=framework,
                    outline_sections=outline_sections,
                    source_key=source_key,
                    source_md=source_md[:_MAX_SOURCE_CHARS],
                    source_vault_hashes=source_vault_hashes,
                    current_json=json.dumps(current, indent=2),
                    issues=issues,
                )
                try:
                    rr, rm = await chat_judge_bandit_async(
                        repair_prompt,
                        max_tokens=_MAX_TOKENS_REPAIR,
                        temperature=_TEMPERATURE_REPAIR,
                        response_format=_DIGEST_RESPONSE_FORMAT,
                    )
                    deployment = (rm or {}).get("deployment") or deployment
                    rp = _parse_json_response(rr)
                    if not rp:
                        break
                    new_payload, new_err = _try_parse_payload(rp)
                    if new_payload is None:
                        break
                    new_issues = validate_source_digest(
                        new_payload,
                        valid_section_ids=valid_section_ids,
                        valid_vault_hashes=valid_hash_set,
                    )
                    # Only accept if it actually improves
                    if len(new_issues) <= len(issues):
                        payload = new_payload
                        current = payload.model_dump()
                        issues = new_issues
                    else:
                        break
                except Exception as e:
                    logger.warning(
                        f"[digest_construct] {source_key}: content-"
                        f"repair attempt {attempt} failed: "
                        f"{type(e).__name__}: {e}"
                    )
                    break

            # If issues remain, we KEEP the digest anyway but log a
            # warning — `build_per_section_index` silently drops
            # unknown section_id contribs, so the aggregate stays
            # consistent. mgsr_replan can flag the source for revisit.
            if issues:
                logger.info(
                    f"[digest_construct] {source_key}: kept with "
                    f"{len(issues)} unresolved issues: {issues[0][:80]}"
                )

        # Fallback for ugly LLM titles
        if (
            not payload.source_title
            or payload.source_title.lower() in {"untitled", "n/a", "none"}
        ):
            payload.source_title = derive_source_title_fallback(
                source_md, source_key
            )

        wall_ms = int((time.monotonic() - t0) * 1000)
        src_digest = SourceDigest(
            source_key=source_key,
            source_title=payload.source_title,
            overall_summary=payload.overall_summary,
            contributes_to=payload.contributes_to,
            unassigned_code_refs=payload.unassigned_code_refs,
            deployment=deployment,
            wall_ms=wall_ms,
        )
        await emit_progress(
            thread_id, "digest_construct", "source_done",
            sample_idx=sample_idx, n_total=n_total,
            source_key=source_key, ok=True,
            n_contributions=len(payload.contributes_to),
            n_unassigned=len(payload.unassigned_code_refs),
            wall_ms=wall_ms,
            deployment=deployment,
        )
        return src_digest


# =============================================================================
# Manifest hash
# =============================================================================
def _compute_manifest_hash(
    *,
    outline_manifest_hash: str,
    source_keys: list[str],
    sources_bytes: int,
) -> str:
    payload = (
        f"outline={outline_manifest_hash}|"
        f"sources={','.join(sorted(source_keys))}|"
        f"n={len(source_keys)}|"
        f"bytes={sources_bytes}|"
        f"prompt={DIGEST_PROMPT_VERSION}|"
        f"schema={DIGEST_SCHEMA_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# The node
# =============================================================================
@traced("digest_construct")
async def digest_construct(state: SynthState) -> dict:
    """Run the LLM-assigned source-to-section router for one chapter."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "digest_path":  "",
            "digest_stats": {
                "skipped": "no_slug_or_chapter_id", "wall_ms": 0,
            },
            "status": "failed",
            "error":  "framework_slug or chapter_id missing from SynthState",
        }

    t0 = time.monotonic()
    minio = get_storage()

    # ── Load outline blob (the canonical input — has outline + sources) ─
    outline_key = _outline_latest_key(slug, chapter_id)
    if not await minio.exists(outline_key):
        return {
            "digest_path":  "",
            "digest_stats": {
                "skipped": "outline_not_found",
                "outline_key": outline_key,
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  (
                f"outline {outline_key!r} not in MinIO; run outline_sdp first"
            ),
        }

    try:
        outline_text = await minio.read_text(outline_key)
        outline_payload = json.loads(outline_text)
    except Exception as e:
        return {
            "digest_path":  "",
            "digest_stats": {
                "skipped": "outline_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"outline-latest.json unreadable: {type(e).__name__}: {e}",
        }

    outline_data = outline_payload.get("outline") or {}
    outline_sections = outline_data.get("sections") or []
    source_keys = sorted(outline_payload.get("source_keys") or [])
    chapter_title = outline_payload.get("chapter_title") or chapter_id
    outline_manifest_hash = outline_payload.get("manifest_hash") or ""

    if not outline_sections or not source_keys:
        return {
            "digest_path":  "",
            "digest_stats": {
                "skipped": "empty_outline_or_sources",
                "n_sections": len(outline_sections),
                "n_sources": len(source_keys),
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  (
                f"outline has {len(outline_sections)} sections and "
                f"{len(source_keys)} sources — both must be >0"
            ),
        }

    valid_section_ids = {s["section_id"] for s in outline_sections}

    await emit_progress(
        thread_id, "digest_construct", "start",
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        n_sections=len(outline_sections),
        n_sources=len(source_keys),
    )

    # ── Read all source bodies in parallel ─────────────────────────────
    bodies = await minio.read_many(source_keys)

    # ── Runtime sentinelization fallback (2026-05-24 evening fix) ──────
    # When ingestion produced per-page markdown files at
    # `ingestion/{slug}/pages/...` but didn't build matching per-page
    # vaults (e.g., the consolidated llms-full crawl built only one mega-
    # vault), the per-source markdown has no sentinels and the digest
    # LLM finds zero vault hashes → emits empty code_refs → sawc ends up
    # with allowed_hashes=[] → final chapter has zero code blocks.
    # Fix: sentinelize each source on-the-fly here. The resulting
    # vault entries are accumulated below and the sentinelized bodies
    # replace the raw ones for downstream LLM input.
    from ..vault.service import sentinelize_doc as _sentinelize_doc
    runtime_vault_entries: dict = {}
    sentinelized_bodies: list[bytes | str | None] = []
    for sk, raw_body in zip(source_keys, bodies):
        if not raw_body:
            sentinelized_bodies.append(raw_body)
            continue
        body_text = (
            raw_body.decode("utf-8", errors="replace")
            if isinstance(raw_body, (bytes, bytearray))
            else raw_body
        )
        if "<code-ref hash=" in body_text:
            # Already sentinelized at the source (the pre-built path
            # would have produced this).
            sentinelized_bodies.append(body_text)
            continue
        try:
            sentinelized, entries = _sentinelize_doc(body_text)
            sentinelized_bodies.append(sentinelized)
            # Convert VaultEntry → dict for downstream JSON serialization
            for h, e in entries.items():
                if h not in runtime_vault_entries:
                    runtime_vault_entries[h] = (
                        e.model_dump() if hasattr(e, "model_dump") else dict(e)
                    )
        except Exception as exc:
            logger.warning(
                f"[digest_construct] runtime-sentinelize failed for "
                f"{sk!r}: {type(exc).__name__}: {exc}; using raw body"
            )
            sentinelized_bodies.append(body_text)
    bodies = sentinelized_bodies
    if runtime_vault_entries:
        logger.info(
            f"[digest_construct] {slug}/{chapter_id}: runtime-sentinelized "
            f"{sum(1 for b in bodies if b and '<code-ref hash=' in str(b))} "
            f"sources, accumulated {len(runtime_vault_entries)} vault entries"
        )

    # Pair (source_key, body); drop empties + log
    pairs: list[tuple[str, str]] = []
    for k, b in zip(source_keys, bodies):
        if b:
            pairs.append((k, b))
        else:
            logger.warning(
                f"[digest_construct] empty body for source {k!r}; skipping"
            )

    if not pairs:
        return {
            "digest_path":  "",
            "digest_stats": {
                "skipped": "all_sources_empty",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  "every source body returned empty from MinIO",
        }

    total_bytes = sum(len(b) for _, b in pairs)
    # Aggregate vault hashes across all sources for the orphan check
    all_vault_hashes: set[str] = set()
    for _, body in pairs:
        all_vault_hashes.update(extract_vault_hashes(body))

    await emit_progress(
        thread_id, "digest_construct", "outline_loaded",
        n_sections=len(outline_sections),
        n_sources=len(pairs),
        n_total_vault_hashes=len(all_vault_hashes),
        total_bytes=total_bytes,
    )

    # ── Cache fast-path ────────────────────────────────────────────────
    manifest_hash = _compute_manifest_hash(
        outline_manifest_hash=outline_manifest_hash,
        source_keys=[k for k, _ in pairs],
        sources_bytes=total_bytes,
    )
    versioned_key = _versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = _latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            cov = (cached or {}).get("coverage_stats") or {}
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_sources":            len(cached.get("per_source") or []),
                "n_sections":           cov.get("n_sections", 0),
                "n_sections_covered":   cov.get("sections_with_primary", 0),
                "n_empty_sections":     len(cov.get("empty_sections") or []),
                "n_merged_sections":    len(cached.get("merged_sections") or {}),
                "merged_sections":      cached.get("merged_sections") or {},
                "n_over_spread":        len(cov.get("over_spread_sources") or []),
                "n_orphan_code_refs":   cov.get("orphan_code_refs", 0),
                "n_pydantic_fail":      cached.get("n_pydantic_fail", 0),
                "wall_ms":              elapsed,
                "store_path":           latest_key,
                "versioned_path":       versioned_key,
                "manifest_hash":        manifest_hash,
                "cache_hit":            True,
                "prompt_version":       cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "digest_construct", "done",
                n_sources=stats["n_sources"],
                n_sections=stats["n_sections"],
                n_sections_covered=stats["n_sections_covered"],
                n_empty_sections=stats["n_empty_sections"],
                n_orphan_code_refs=stats["n_orphan_code_refs"],
                wall_ms=elapsed, cache_hit=True,
            )
            logger.info(
                f"[digest_construct] {slug}/{chapter_id}: CACHE HIT — "
                f"{stats['n_sources']} sources, "
                f"{stats['n_sections_covered']}/{stats['n_sections']} "
                f"sections covered, {elapsed} ms"
            )
            return {"digest_path": latest_key, "digest_stats": stats}
        except Exception as e:
            logger.warning(
                f"[digest_construct] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    # ── Fire per-source LLM calls (bounded concurrency) ───────────────
    sem = asyncio.Semaphore(_CONCURRENCY)
    tasks = [
        _digest_one_source(
            sem=sem,
            sample_idx=i,
            n_total=len(pairs),
            thread_id=thread_id,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            framework=slug,
            outline_sections=outline_sections,
            valid_section_ids=valid_section_ids,
            source_key=key,
            source_md=body,
        )
        for i, (key, body) in enumerate(pairs)
    ]
    results = await asyncio.gather(*tasks)
    per_source: list[SourceDigest] = [r for r in results if r is not None]
    n_pydantic_fail = sum(1 for r in results if r is None)

    await emit_progress(
        thread_id, "digest_construct", "digests_aggregated",
        n_digests_ok=len(per_source),
        n_pydantic_fail=n_pydantic_fail,
        n_total=len(pairs),
    )

    # ── Aggregate per-section index + coverage stats (deterministic) ──
    section_ids = [s["section_id"] for s in outline_sections]

    # Fix #3 (DD-SYNTH-SECTION-COUNT, 2026-05-29 PM) — source-pool merge.
    # Fold sections whose PRIMARY source pools overlap heavily into one
    # (the definitive overlap signal; see merge_overlapping_sections). The
    # returned per_source has losing contributions re-tagged to their
    # winner, so the rebuilt per_section index naturally has losers empty;
    # sawc_write skips merged sections so they never render as hollow
    # cross-references.
    per_source, merged_sections = merge_overlapping_sections(
        per_source, outline_sections,
    )
    if merged_sections:
        logger.info(
            f"[digest_construct] {slug}/{chapter_id}: source-pool merge "
            f"folded {len(merged_sections)} section(s) → "
            f"{sorted(set(merged_sections.values()))} "
            f"(losers: {sorted(merged_sections)})"
        )

    per_section = build_per_section_index(per_source, section_ids)
    coverage = compute_coverage_stats(
        per_source=per_source,
        per_section=per_section,
        section_ids=section_ids,
        all_vault_hashes=list(all_vault_hashes),
    )

    # ── Persist ChapterDigest ─────────────────────────────────────────
    chapter_digest = ChapterDigest(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        framework_slug=slug,
        n_pydantic_fail=n_pydantic_fail,
        per_source=per_source,
        per_section=per_section,
        coverage_stats=coverage,
        merged_sections=merged_sections,
    )
    payload = chapter_digest.model_dump()
    payload["outline_manifest_hash"] = outline_manifest_hash
    payload["digest_manifest_hash"]  = manifest_hash
    payload["source_keys"]           = [k for k, _ in pairs]
    payload["n_total_vault_hashes"]  = len(all_vault_hashes)

    blob_bytes = json.dumps(payload, indent=2, ensure_ascii=False)
    await minio.write(
        versioned_key, blob_bytes, content_type="application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type="application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_sources":            len(per_source),
        "n_sections":           coverage.n_sections,
        "n_sections_covered":   coverage.sections_with_primary,
        "n_empty_sections":     len(coverage.empty_sections),
        "empty_sections":       coverage.empty_sections,
        "n_merged_sections":    len(merged_sections),
        "merged_sections":      merged_sections,
        "n_over_spread":        len(coverage.over_spread_sources),
        "over_spread_sources":  coverage.over_spread_sources,
        "n_orphan_code_refs":   coverage.orphan_code_refs,
        "n_total_vault_hashes": len(all_vault_hashes),
        "n_pydantic_fail":      n_pydantic_fail,
        "avg_sources_per_section": coverage.avg_sources_per_section,
        "avg_sections_per_source": coverage.avg_sections_per_source,
        "wall_ms":              elapsed,
        "store_path":           latest_key,
        "versioned_path":       versioned_key,
        "manifest_hash":        manifest_hash,
        "cache_hit":            False,
        "prompt_version":       DIGEST_PROMPT_VERSION,
    }
    await emit_progress(
        thread_id, "digest_construct", "done",
        n_sources=stats["n_sources"],
        n_sections=stats["n_sections"],
        n_sections_covered=stats["n_sections_covered"],
        n_empty_sections=stats["n_empty_sections"],
        n_merged_sections=stats["n_merged_sections"],
        n_orphan_code_refs=stats["n_orphan_code_refs"],
        n_pydantic_fail=n_pydantic_fail,
        wall_ms=elapsed,
    )
    logger.info(
        f"[digest_construct] {slug}/{chapter_id}: "
        f"{stats['n_sources']}/{len(pairs)} sources digested, "
        f"{stats['n_sections_covered']}/{stats['n_sections']} sections "
        f"with primary, {stats['n_empty_sections']} empty, "
        f"{stats['n_orphan_code_refs']} orphan refs, {elapsed} ms"
    )
    return {"digest_path": latest_key, "digest_stats": stats}


# =============================================================================
# Convenience loader for downstream nodes
# =============================================================================
def load_digest_payload(text: str) -> dict:
    """Parse the persisted digest blob. Returns the full payload dict;
    downstream nodes pick the fields they need (per_section,
    coverage_stats, etc.)."""
    return json.loads(text)
