"""Substep 8 — plan_write: persist the FINAL chapter plan to MinIO.

Per `docs/PLANNER-ARCHITECTURE-2026-05-17.md` + May 2026 SOTA report
(SurveyGen-I arXiv 2508.14317 + SurveyForge arXiv 2503.04629 +
LLMxMapReduce-V2 arXiv 2504.05732 + TnT-LLM arXiv 2403.12173 +
Atlas/SLSA v1.1 provenance idioms). Pipeline:

  1. Load reduce outline + refine assignments + cluster keys + labels.
  2. Hydrate each chapter's `sources` from refined cluster_id → MinIO
     doc-key map (flat array of keys per SurveyForge / LLMxMapReduce —
     downstream chapter synthesizer does its own read_many()).
  3. Light sanitization (~no LLM): smart title-case, description trim
     and clamp, drop chapters with empty sources, re-number `order`
     1..N contiguous, generate stable `id = ch-{order}-{slug}`.
  4. Embed upstream provenance refs inline (5 *_ref pointers + prompt
     versions + corpus_doc_count) per Atlas/SLSA "consumer-facing
     artifact carries digests of its inputs" pattern.
  5. Write the hash-keyed versioned blob at
     `planner/{slug}/plan/{hash}.json`, then PUT a mutable latest
     pointer at `planner/{slug}/plan-latest.json` (MinIO/S3 has no
     symlink — small mutable object is the idiomatic move).

State writes:
  plan_path — MinIO key of the LATEST pointer (the consumer-facing key)

Notes:
- NO Self-Refine pass on the outline (Madaan 2023 gains are on
  creative/code, not on already-validated structural outputs;
  SurveyForge refines during writing, not post-hoc). Skipped per
  research recommendation — spend the rotator budget on chapter
  synthesis instead.
- The reduce node already produces a content-addressed hash-keyed
  blob at `planner/{slug}/chapters/{hash}.json`; this node produces
  a CONSUMER-facing artifact with file lists hydrated.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from hashlib import sha256

import numpy as np

from services.docs_distiller.ingestion.storage_minio import get_storage

from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState
from .cluster import load_clusters
from .label import load_labels
from .reduce import load_outline
from .refine import load_refine


logger = logging.getLogger(__name__)


_SCHEMA_VERSION = "1.0"
_PROMPT_VERSION = "v1-2026-05-18"
_BLOB_PREFIX    = "planner"
_DESCRIPTION_MAX_CHARS = 400
_TITLE_MAX_WORDS       = 10
_SLUG_MAX_WORDS        = 6

# Words that should stay lowercase in titles unless first/last. Keep
# tight — sanitization is best-effort, not authoritative.
_TITLE_LOWERCASE = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in",
    "is", "of", "on", "or", "the", "to", "vs", "with",
}
# Words that should always stay UPPERCASE (acronyms commonly in docs).
_TITLE_UPPERCASE = {
    "api", "apis", "cli", "cdk", "css", "html", "http", "https",
    "io", "json", "jwt", "k8s", "rpc", "sdk", "sql", "ssl", "tcp",
    "tls", "url", "uri", "ui", "uuid", "xml", "yaml", "ai", "llm",
    "ml", "nlp", "ux", "ide", "orm", "rest", "ssh", "tls",
}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _versioned_blob_key(slug: str, manifest_hash: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/plan/{manifest_hash}.json"


def _latest_blob_key(slug: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/plan-latest.json"


def _smart_title_case(s: str) -> str:
    """Title-case that preserves common acronyms (API/CLI/SDK/...) and
    lowercases linking words (of/and/the/...). First+last words always
    capitalize. Falls through cleanly on already-Title-Case input."""
    raw = (s or "").strip()
    if not raw:
        return ""
    words = raw.split()
    out: list[str] = []
    for i, w in enumerate(words):
        low = w.lower()
        if low in _TITLE_UPPERCASE:
            out.append(low.upper())
            continue
        if low in _TITLE_LOWERCASE and 0 < i < len(words) - 1:
            out.append(low)
            continue
        # Preserve internal-cap words (e.g. "LangGraph", "ZeroMQ")
        # if the input is mixed-case; otherwise smart-capitalize.
        if any(c.isupper() for c in w[1:]):
            out.append(w)
        else:
            out.append(low[:1].upper() + low[1:])
    return " ".join(out)


def _slugify(s: str) -> str:
    """ASCII-lowercase slug for stable chapter IDs."""
    low = (s or "").strip().lower()
    if not low:
        return "chapter"
    parts = [p for p in _SLUG_RE.sub("-", low).split("-") if p]
    return "-".join(parts[:_SLUG_MAX_WORDS]) or "chapter"


def _trim_description(desc: str) -> str:
    cleaned = " ".join((desc or "").strip().split())
    if len(cleaned) <= _DESCRIPTION_MAX_CHARS:
        return cleaned
    cut = cleaned[: _DESCRIPTION_MAX_CHARS - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"


def _build_cluster_to_keys(
    refined_assignments: np.ndarray, keys: list[str],
) -> dict[int, list[str]]:
    """Group MinIO doc keys by refined cluster_id. Noise (-1) included
    so the sanitizer can decide whether to drop it."""
    out: dict[int, list[str]] = {}
    n = min(len(keys), int(refined_assignments.shape[0]))
    for i in range(n):
        cid = int(refined_assignments[i])
        out.setdefault(cid, []).append(keys[i])
    for cid in out:
        out[cid] = sorted(set(out[cid]))
    return out


def _sanitize_chapters(
    outline_chapters: list[dict],
    cluster_to_keys: dict[int, list[str]],
) -> tuple[list[dict], int]:
    """Hydrate sources + title/description cleanup + drop empty +
    re-number. Returns (chapters, n_dropped)."""
    raw_sorted = sorted(
        (c for c in outline_chapters if isinstance(c, dict)),
        key=lambda c: (c.get("order") or 999, c.get("title") or ""),
    )

    sanitized: list[dict] = []
    dropped = 0
    seen_global_keys: set[str] = set()

    for ch in raw_sorted:
        member_ids = []
        for cid in (ch.get("member_cluster_ids") or []):
            try:
                member_ids.append(int(cid))
            except (TypeError, ValueError):
                continue

        # Hydrate sources from refined assignments. Dedup across the
        # whole plan — a doc must appear in AT MOST ONE chapter, even
        # if reduce duplicated a cluster id (shouldn't happen post
        # reduce's coverage repair, but cheap defense).
        sources: list[str] = []
        for cid in member_ids:
            for key in cluster_to_keys.get(cid, []):
                if key in seen_global_keys:
                    continue
                seen_global_keys.add(key)
                sources.append(key)

        if not sources:
            dropped += 1
            continue

        title = _smart_title_case(ch.get("title") or "Untitled Chapter")
        # Hard cap on title length — pathologically long LLM outputs.
        words = title.split()
        if len(words) > _TITLE_MAX_WORDS:
            title = " ".join(words[:_TITLE_MAX_WORDS])

        sanitized.append({
            "title":              title,
            "description":        _trim_description(ch.get("description") or ""),
            "member_cluster_ids": member_ids,
            "sources":            sorted(sources),
            "n_sources":          len(sources),
        })

    # Re-number `order` to 1..N contiguous + assign stable slug ids.
    for i, ch in enumerate(sanitized, start=1):
        ch["order"] = i
        ch["id"] = f"ch-{i:02d}-{_slugify(ch['title'])}"

    return sanitized, dropped


def _compute_manifest_hash(
    cluster_ref: str, refine_ref: str, labels_ref: str,
    reduce_ref: str, schema_version: str,
) -> str:
    payload = (
        f"cluster={cluster_ref}|refine={refine_ref}|"
        f"labels={labels_ref}|reduce={reduce_ref}|"
        f"schema={schema_version}|prompt={_PROMPT_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


@traced("plan_write")
async def plan_write(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    cluster_ref = state.get("cluster_assignments_ref") or ""
    refine_ref = state.get("refine_assignments_ref") or ""
    labels_ref = state.get("cluster_labels_ref") or ""
    reduce_ref = state.get("chapter_plan_ref") or ""
    embeddings_ref = state.get("embeddings_ref") or ""

    if not slug or not cluster_ref or not refine_ref or not reduce_ref:
        return {
            "plan_path": "",
            "status": "done",
        }

    t0 = time.monotonic()

    manifest_hash = _compute_manifest_hash(
        cluster_ref, refine_ref, labels_ref, reduce_ref, _SCHEMA_VERSION,
    )
    versioned_key = _versioned_blob_key(slug, manifest_hash)
    latest_key = _latest_blob_key(slug)
    minio = get_storage()

    # Emit `start` unconditionally so the UI shows a live "running"
    # status line even on cache hit (other nodes follow the same
    # convention — see label.py / reduce.py SSE flow).
    await emit_progress(
        thread_id, "plan_write", "start",
        manifest_hash=manifest_hash,
    )

    # ── Cache fast-path ────────────────────────────────────────────────
    # BOTH the hash-keyed blob AND the latest pointer must exist; if the
    # latest pointer is missing or points to a different hash, we
    # rewrite it.
    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            latest_text = await minio.read_text(latest_key)
            latest = json.loads(latest_text) or {}
            if latest.get("manifest_hash") == manifest_hash:
                chapters = latest.get("chapters") or []
                latest_stats = latest.get("stats") or {}
                n_sources = sum(
                    len(c.get("sources") or []) for c in chapters
                )
                n_unassigned_cached = len(latest.get("unassigned") or [])
                elapsed = int((time.monotonic() - t0) * 1000)
                stats = {
                    "n_chapters":     len(chapters),
                    "n_sources":      n_sources,
                    "n_unassigned":   latest_stats.get(
                        "n_unassigned", n_unassigned_cached,
                    ),
                    "n_dropped":      latest_stats.get("n_dropped", 0),
                    "wall_ms":        elapsed,
                    "store_path":     latest_key,
                    "versioned_path": versioned_key,
                    "manifest_hash":  manifest_hash,
                    "cache_hit":      True,
                    "plan":           latest,
                }
                await emit_progress(
                    thread_id, "plan_write", "done",
                    n_chapters=len(chapters),
                    n_sources=n_sources,
                    n_unassigned=stats["n_unassigned"],
                    n_dropped=stats["n_dropped"],
                    wall_ms=elapsed, cache_hit=True,
                )
                logger.info(
                    f"[plan_write] {slug}: CACHE HIT — {len(chapters)} "
                    f"chapters, {n_sources} sources, {elapsed} ms"
                )
                return {"plan_path": latest_key, "plan_write_stats": stats,
                        "status": "done"}
        except Exception as e:
            logger.warning(
                f"[plan_write] {slug}: cached latest unreadable "
                f"({type(e).__name__}: {e}); regenerating"
            )

    # ── Load upstream artifacts ────────────────────────────────────────
    cluster_blob = await minio.read_bytes(cluster_ref)
    cluster_keys, _orig_assigns, _max_probs, _soft = load_clusters(cluster_blob)
    refine_blob = await minio.read_bytes(refine_ref)
    refine_keys, refined_assignments, _, _ = load_refine(refine_blob)
    if cluster_keys != refine_keys:
        raise RuntimeError(
            f"plan_write: key mismatch — cluster has {len(cluster_keys)} "
            f"keys, refine has {len(refine_keys)}; pipeline integrity broken"
        )
    labels_text = await minio.read_text(labels_ref)
    labels = load_labels(labels_text)
    reduce_text = await minio.read_text(reduce_ref)
    outline = load_outline(reduce_text)

    await emit_progress(
        thread_id, "plan_write", "loaded",
        n_chapters_in=len((outline or {}).get("chapters") or []),
        n_clusters=len({int(c) for c in refined_assignments if int(c) >= 0}),
        n_docs=len(cluster_keys),
    )

    # ── Hydrate + sanitize ─────────────────────────────────────────────
    cluster_to_keys = _build_cluster_to_keys(refined_assignments, cluster_keys)
    raw_chapters = (outline or {}).get("chapters") or []
    chapters, n_dropped = _sanitize_chapters(raw_chapters, cluster_to_keys)
    n_sources_total = sum(len(c["sources"]) for c in chapters)

    # Account for docs that ended up in NO chapter (cluster id reduce
    # never assigned, or noise that was orphaned).
    unassigned_keys = sorted(set(cluster_keys) - {
        k for c in chapters for k in c["sources"]
    })

    await emit_progress(
        thread_id, "plan_write", "sanitized",
        n_chapters=len(chapters), n_dropped=n_dropped,
        n_sources=n_sources_total, n_unassigned=len(unassigned_keys),
    )

    # ── Build the consumer-facing payload ──────────────────────────────
    plan = {
        "schema_version": _SCHEMA_VERSION,
        "framework_slug": slug,
        "manifest_hash":  manifest_hash,
        "generated_at":   datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ",
        ),
        "chapters":       chapters,
        "unassigned":     unassigned_keys,
        "provenance": {
            "embeddings_ref":  embeddings_ref,
            "cluster_ref":     cluster_ref,
            "refine_ref":      refine_ref,
            "labels_ref":      labels_ref,
            "reduce_ref":      reduce_ref,
            "prompt_versions": {"plan_write": _PROMPT_VERSION},
            "corpus_doc_count": len(cluster_keys),
            "cluster_count":   len({
                int(c) for c in refined_assignments if int(c) >= 0
            }),
            "label_count":     sum(1 for lid in labels if int(lid) >= 0),
        },
        "stats": {
            "n_chapters":   len(chapters),
            "n_sources":    n_sources_total,
            "n_unassigned": len(unassigned_keys),
            "n_dropped":    n_dropped,
        },
    }

    # ── Persist: hash-keyed + latest pointer ───────────────────────────
    plan_bytes = json.dumps(plan, indent=2, ensure_ascii=False)
    await minio.write(
        versioned_key, plan_bytes, content_type="application/json",
    )
    await minio.write(
        latest_key, plan_bytes, content_type="application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_chapters":     len(chapters),
        "n_sources":      n_sources_total,
        "n_unassigned":   len(unassigned_keys),
        "n_dropped":      n_dropped,
        "wall_ms":        elapsed,
        "store_path":     latest_key,
        "versioned_path": versioned_key,
        "manifest_hash":  manifest_hash,
        "cache_hit":      False,
        "plan":           plan,
    }
    await emit_progress(
        thread_id, "plan_write", "done",
        n_chapters=len(chapters), n_sources=n_sources_total,
        n_unassigned=len(unassigned_keys), n_dropped=n_dropped,
        wall_ms=elapsed,
    )
    logger.info(
        f"[plan_write] {slug}: {len(chapters)} chapters, "
        f"{n_sources_total} sources, {n_dropped} dropped, "
        f"{len(unassigned_keys)} unassigned; wrote {latest_key} + "
        f"{versioned_key} in {elapsed} ms"
    )
    return {"plan_path": latest_key, "plan_write_stats": stats,
            "status": "done"}
