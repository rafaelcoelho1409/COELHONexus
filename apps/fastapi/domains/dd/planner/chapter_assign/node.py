"""chapter_assign — per-doc LLM assigns confidence scores to each chapter.

Pipeline:
  1. Load proposals + distillates (or raw bodies for small N).
  2. Parallel LLM calls (concurrency 16) — one per doc, scores each chapter.
  3. Persist sparse matrix as MinIO JSON.

State writes:
  chapter_doc_assignments_ref — MinIO key of the JSON
  assign_stats                — counts, coverage stats
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from hashlib import sha256
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ...ingestion.storage import get_storage
from domains.llm.rotator.chain import chat_judge_bandit_async

from ..chapter_propose import load_proposals
from ..doc_distill import load_distillates
from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState


logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# Concurrency for per-doc scoring.
# 2026-05-27 P1 — lowered 16 → 8 to match doc_distill after Claude Code
# Run produced 14% rate-limit failures with 16-way concurrency. Same
# diagnosis (NIM+Mistral saturation), same fix.
_CONCURRENCY = 8

# LLM call settings (per-doc scoring is short).
_MAX_TOKENS = 600
_TEMPERATURE = 0.0
_MAX_REPAIR_ATTEMPTS = 1

# Per-doc body cap when no distillate is available.
_BODY_CHARS = 4_000

# Confidence threshold for considering a doc "assigned" to a chapter.
# chapter_select uses this to gate which assignments count for coverage.
_CONFIDENCE_THRESHOLD = 0.5

_BLOB_PREFIX = "planner"
_PROMPT_VERSION = "v1-2026-05-27"


class ChapterScore(BaseModel):
    """One score for one chapter."""
    chapter_idx: int = Field(description="Index into the proposals list.")
    confidence: float = Field(
        description=(
            "0.0-1.0 confidence that this doc belongs to this chapter. "
            "Set 0.0 for chapters with no relevance; set 0.5+ only when "
            "the doc materially supports the chapter."
        ),
    )

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        if v < 0.0: return 0.0
        if v > 1.0: return 1.0
        return float(v)


class DocAssignment(BaseModel):
    """LLM output for ONE doc — confidence against each chapter."""
    scores: list[ChapterScore] = Field(
        description=(
            "ONE score entry per chapter proposal (in the same order as "
            "the chapters list shown in the prompt)."
        ),
    )


_ASSIGN_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "doc_assignment",
        "schema": DocAssignment.model_json_schema(),
        "strict": False,
    },
}


def _parse(raw: str) -> Optional[dict]:
    if not raw: return None
    m = _JSON_RE.search(raw)
    if not m: return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _build_prompt(
    *,
    framework: str,
    source_key: str,
    doc_summary: str,
    doc_terms: list[str],
    doc_body: str,
    proposals: list[dict],
) -> str:
    chapters_block = "\n".join([
        f"[{i}] {p.get('title')!r}\n"
        f"    description: {p.get('description', '')}\n"
        f"    key_concepts: {', '.join((p.get('key_concepts') or [])[:10])}"
        for i, p in enumerate(proposals)
    ])
    if doc_summary:
        doc_block = (
            f"SUMMARY: {doc_summary}\n"
            f"KEY_TERMS: {', '.join(doc_terms[:8])}"
        )
    else:
        body_snip = (doc_body or "")[:_BODY_CHARS]
        doc_block = f"BODY (truncated):\n{body_snip}"
    # V6 (2026-05-28) — prompt-prefix reordering for KV cache hits. The
    # chapter list + scoring rubric are IDENTICAL across all 135+ doc
    # calls in a single run, so they go FIRST as a cacheable prefix.
    # The per-doc file info (the only thing that varies) goes LAST.
    # Providers with prefix-KV-cache (Groq, Gemini implicit, DeepSeek,
    # NIM) get warm hits after the first call.
    return (
        # ── STATIC PREFIX (KV-cacheable across all docs in this corpus) ──
        f"You are assigning ONE documentation file to chapters in a "
        f"{framework} learning book. The file may belong to multiple "
        f"chapters (multi-assignment) or none.\n\n"
        f"== AVAILABLE CHAPTERS ==\n"
        f"{chapters_block}\n"
        f"== END CHAPTERS ==\n\n"
        f"For EACH chapter (in order), output a confidence score:\n"
        f"  0.0 → this doc is unrelated\n"
        f"  0.3 → tangential mention\n"
        f"  0.7 → primary supporting doc\n"
        f"  1.0 → canonical reference for this chapter\n\n"
        f"OUTPUT — STRICT JSON:\n"
        f'{{"scores": [{{"chapter_idx": 0, "confidence": <float>}}, '
        f'{{"chapter_idx": 1, "confidence": <float>}}, ...]}}\n\n'
        f"Cover EVERY chapter (one entry per chapter index, including "
        f"0.0 scores). Be honest — most docs only belong to 1-3 "
        f"chapters.\n\n"
        # ── DYNAMIC SUFFIX (this is the only thing that varies per call) ──
        f"== FILE: {source_key} ==\n"
        f"{doc_block}"
    )


async def _assign_one(
    sem: asyncio.Semaphore,
    minio,
    framework: str,
    source_key: str,
    distillate: Optional[dict],
    proposals: list[dict],
) -> tuple[str, Optional[list[dict]], int]:
    """Returns (source_key, scores_list_or_None, wall_ms)."""
    async with sem:
        t0 = time.monotonic()
        doc_summary = (distillate or {}).get("summary") or ""
        doc_terms = (distillate or {}).get("key_terms") or []
        doc_body = ""
        if not doc_summary:
            try:
                doc_body = await minio.read_text(source_key)
            except Exception:
                pass
            if not doc_body:
                return source_key, None, int((time.monotonic() - t0) * 1000)

        prompt = _build_prompt(
            framework=framework, source_key=source_key,
            doc_summary=doc_summary, doc_terms=doc_terms,
            doc_body=doc_body, proposals=proposals,
        )

        try:
            # 2026-05-27 — route through `dd-reduce-label` (non-reasoning
            # curated pool: Groq Llama-3.3-70B, Gemini Flash-Lite,
            # Nemotron-3-super, gpt-oss-120b, Mistral Large/Small,
            # Llama-4 Maverick) instead of the default `dd-grader` which
            # the FGTS-VA bandit was over-favoring to reasoning models
            # (GLM-5.1 hit 51% of calls in prior runs). For short
            # structured JSON scoring, reasoning model <think> blocks
            # add 10-25s overhead per call — wrong tool. Per-call
            # latency drops 1-3s vs 10-25s; total wall-time on this
            # stage drops ~5-10×.
            raw, _ = await chat_judge_bandit_async(
                prompt,
                max_tokens=_MAX_TOKENS,
                temperature=_TEMPERATURE,
                response_format=_ASSIGN_RESPONSE_FORMAT,
                dd_process="dd-reduce-label",
            )
        except Exception as e:
            logger.warning(
                f"[chapter_assign] LLM failed for {source_key}: "
                f"{type(e).__name__}: {e}"
            )
            return source_key, None, int((time.monotonic() - t0) * 1000)

        parsed = _parse(raw)
        if not parsed:
            return source_key, None, int((time.monotonic() - t0) * 1000)
        try:
            assignment = DocAssignment.model_validate(parsed)
        except Exception:
            return source_key, None, int((time.monotonic() - t0) * 1000)

        # Sanitize: keep only valid chapter_idx + clamp confidence.
        n_proposals = len(proposals)
        scores = [
            {"chapter_idx": s.chapter_idx, "confidence": s.confidence}
            for s in assignment.scores
            if 0 <= s.chapter_idx < n_proposals
        ]
        return source_key, scores, int((time.monotonic() - t0) * 1000)


def _manifest_hash(*, slug: str, proposals_ref: str, source_keys: list[str]) -> str:
    h = sha256()
    h.update(_PROMPT_VERSION.encode())
    h.update(slug.encode())
    h.update(b"|"); h.update(proposals_ref.encode())
    for k in sorted(source_keys):
        h.update(b"|"); h.update(k.encode())
    return h.hexdigest()[:16]


def _versioned_key(slug: str, manifest: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/chapter_assign/{manifest}.json"


def _latest_key(slug: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/chapter_assign-latest.json"


async def load_assignments(minio, slug: str) -> dict:
    """Returns {source_key: [{chapter_idx, confidence}, ...]}."""
    try:
        text = await minio.read_text(_latest_key(slug))
        data = json.loads(text)
        return data.get("assignments") or {}
    except Exception:
        return {}


@traced("chapter_assign")
async def chapter_assign(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    relevant_files = state.get("relevant_files") or state.get("raw_files") or []
    proposals_ref = state.get("chapter_proposals_ref")

    if not slug or not relevant_files or not proposals_ref:
        return {
            "chapter_doc_assignments_ref": None,
            "assign_stats": {"skipped": "missing_inputs"},
        }

    t0 = time.monotonic()
    minio = get_storage()
    proposals_obj = await load_proposals(minio, slug)
    if proposals_obj is None or not proposals_obj.proposals:
        return {
            "chapter_doc_assignments_ref": None,
            "assign_stats": {"skipped": "no_proposals_loaded"},
        }
    proposals_dicts = [p.model_dump() for p in proposals_obj.proposals]
    distillates = await load_distillates(minio, slug)

    # Cache fast-path.
    manifest = _manifest_hash(
        slug=slug, proposals_ref=proposals_ref, source_keys=relevant_files,
    )
    vkey = _versioned_key(slug, manifest)
    lkey = _latest_key(slug)
    if await minio.exists(vkey) and await minio.exists(lkey):
        try:
            cached = json.loads(await minio.read_text(vkey))
            wall_ms = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_docs": len(cached.get("assignments") or {}),
                "n_proposals": len(proposals_dicts),
                "cache_hit": True,
                "wall_ms": wall_ms,
                "manifest_hash": manifest,
            }
            await emit_progress(
                thread_id, "chapter_assign", "done",
                cache_hit=True, n_docs=stats["n_docs"], wall_ms=wall_ms,
            )
            return {"chapter_doc_assignments_ref": lkey, "assign_stats": stats}
        except Exception:
            pass

    await emit_progress(
        thread_id, "chapter_assign", "start",
        n_docs=len(relevant_files), n_proposals=len(proposals_dicts),
    )

    sem = asyncio.Semaphore(_CONCURRENCY)
    tasks = [
        _assign_one(
            sem, minio, slug, k,
            distillates.get(k), proposals_dicts,
        )
        for k in relevant_files
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    assignments: dict[str, list[dict]] = {}
    n_failed = 0
    coverage_count: dict[int, int] = {i: 0 for i in range(len(proposals_dicts))}
    for k, scores, _wall in results:
        if scores is None:
            n_failed += 1
            continue
        assignments[k] = scores
        for s in scores:
            if s["confidence"] >= _CONFIDENCE_THRESHOLD:
                coverage_count[s["chapter_idx"]] = coverage_count.get(
                    s["chapter_idx"], 0,
                ) + 1

    payload = {
        "prompt_version":     _PROMPT_VERSION,
        "framework_slug":     slug,
        "manifest_hash":      manifest,
        "assignments":        assignments,
        "n_docs":             len(relevant_files),
        "n_assigned":         len(assignments),
        "n_failed":           n_failed,
        "n_proposals":        len(proposals_dicts),
        "coverage_count":     coverage_count,
        "confidence_thresh":  _CONFIDENCE_THRESHOLD,
    }
    blob = json.dumps(payload, indent=2, ensure_ascii=False)
    await minio.write(vkey, blob, content_type="application/json")
    await minio.write(lkey, blob, content_type="application/json")

    wall_ms = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_docs": len(relevant_files),
        "n_assigned": len(assignments),
        "n_failed": n_failed,
        "n_proposals": len(proposals_dicts),
        "coverage_count": coverage_count,
        "cache_hit": False,
        "wall_ms": wall_ms,
        "manifest_hash": manifest,
    }
    await emit_progress(
        thread_id, "chapter_assign", "done",
        cache_hit=False, n_assigned=len(assignments), n_failed=n_failed,
        wall_ms=wall_ms,
    )
    return {"chapter_doc_assignments_ref": lkey, "assign_stats": stats}
