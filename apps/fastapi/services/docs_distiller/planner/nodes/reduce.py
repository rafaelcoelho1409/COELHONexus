"""Substep 8 — reduce: merge N labeled clusters into a 4-12 chapter outline.

Per `docs/PLANNER-ARCHITECTURE-2026-05-17.md` + May 2026 SOTA research
(TnT-LLM arXiv 2403.12173 + TopicGPT NAACL 2024 + Universal Self-
Consistency arXiv 2311.17311 + Self-Refine Madaan 2023 + Nature npj-AI
2025 plateau finding). Pipeline:

  1. Load cluster + refine + label artifacts.
  2. For each cluster, build context: label + size + top-5 c-TF-IDF
     keywords + 1 rep-doc first-line.
  3. SINGLE LLM call (NOT iterative pairwise — only pays off at N≥40
     per LLM-Assisted Topic Reduction ECML PKDD 2025). N=3 samples at
     temp=0.3, JSON output.
  4. Universal Self-Consistency vote — one extra LLM call picks the
     best of 3 outlines by coverage + coherence rubric.
  5. ONE self-refine pass (Madaan 2023 FEEDBACK→REFINE). Nature 2025
     npj-AI shows 2 rounds plateau for structured tasks; we use 1.
  6. Coverage post-validate — set-equality on member_cluster_ids vs
     input. Up to 3 repair retries (TnT-LLM reports 12% silent-drop
     rate on raw outputs). Last-resort force-repair dumps orphans
     into Miscellaneous.
  7. Persist as MinIO JSON.

Schema enforced post-parse (same JSON-extract pattern as refine/label —
no `instructor` dep needed):

  chapters: list[{
      title:               str (2-6 words, Title Case noun phrase)
      description:         str (1 sentence)
      member_cluster_ids:  list[int]
      order:               int (1-based)
  }]
  assigned_cluster_ids: list[int]   # TnT-LLM mirror for coverage check

State writes:
  chapter_plan_ref — MinIO key of the JSON blob
  reduce_stats     — observability dict (counts + full outline for UI)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from hashlib import sha256

import numpy as np

from services.docs_distiller.ingestion.storage_minio import get_storage
from services.llm.chain import chat_judge_bandit_async

from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState
from .cluster import load_clusters
from .label import load_labels
from .refine import _compute_cluster_keywords, load_refine


logger = logging.getLogger(__name__)


# Target chapter count — soft target in prompt prose, hard bounds in schema.
_TARGET_K        = 8
_K_MIN           = 4
_K_MAX           = 12
# N samples + USC vote.
_N_SAMPLES       = 3
_TEMPERATURE     = 0.3
# Per-call max_tokens. Outline JSON for 19 clusters → 4-12 chapters with
# titles + descriptions + member lists is comfortably under 4K tokens.
_MAX_TOKENS_OUTLINE = 4000
_MAX_TOKENS_VOTE    = 200
_MAX_TOKENS_REFINE  = 4000
_MAX_TOKENS_REPAIR  = 4000
# c-TF-IDF settings (reuse refine.py's helper).
_KEYWORDS_PER_CLUSTER = 5
_CTFIDF_DOC_CHARS     = 1200
# Rep doc first-line snippet length per cluster (one short line).
_REP_DOC_CHARS = 160
# Coverage repair budget — TnT-LLM reports 12% silent-drop rate; up to 3
# repair retries should handle the vast majority. After that we
# force-repair: dump orphans into Miscellaneous + log a warning.
_MAX_REPAIR_RETRIES = 3
# Cache invalidation bump.
_PROMPT_VERSION  = "v1-2026-05-18"
_BLOB_PREFIX     = "planner"

_MISC_CHAPTER_TITLE = "Miscellaneous"

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _blob_key(slug: str, manifest_hash: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/chapters/{manifest_hash}.json"


def _pick_rep_first_line(
    cluster_id: int,
    refined_assignments: np.ndarray,
    soft: np.ndarray,
    bodies: list[str],
) -> str:
    """First non-empty line of the in-cluster doc with highest soft
    membership for its (refined) cluster."""
    cluster_mask = refined_assignments == cluster_id
    if not cluster_mask.any():
        return ""
    if cluster_id < 0 or cluster_id >= soft.shape[1]:
        idx = int(np.where(cluster_mask)[0][0])
    else:
        membership = soft[:, cluster_id]
        masked = np.where(cluster_mask, membership, -np.inf)
        idx = int(np.argmax(masked))
    body = (bodies[idx] or "").strip()
    if not body:
        return ""
    for line in body.split("\n"):
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:_REP_DOC_CHARS]
    return body[:_REP_DOC_CHARS]


def _parse_response(text: str) -> dict | None:
    """Best-effort JSON extraction. Handles raw JSON + JSON wrapped in
    code-fences. Same pattern as refine/label."""
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


def _validate_outline(
    outline: dict, input_cluster_ids: set[int],
) -> tuple[list[int], list[int], list[int]]:
    """Return (missing_ids, duplicate_ids, unknown_ids)."""
    if not outline or not isinstance(outline, dict):
        return sorted(input_cluster_ids), [], []
    chapters = outline.get("chapters") or []
    seen: dict[int, int] = {}
    for ch in chapters:
        if not isinstance(ch, dict):
            continue
        for cid in (ch.get("member_cluster_ids") or []):
            try:
                cid_int = int(cid)
            except (TypeError, ValueError):
                continue
            seen[cid_int] = seen.get(cid_int, 0) + 1
    seen_ids = set(seen.keys())
    missing = sorted(input_cluster_ids - seen_ids)
    duplicates = sorted([cid for cid, n in seen.items() if n > 1])
    unknown = sorted(seen_ids - input_cluster_ids)
    return missing, duplicates, unknown


def _build_cluster_context_block(
    cluster_id: int, label: str, size: int,
    keywords: list[str], rep_line: str,
) -> str:
    kw_str = ", ".join(keywords[:_KEYWORDS_PER_CLUSTER]) or "(no keywords)"
    line = (rep_line or "").strip().replace("\n", " ")[:_REP_DOC_CHARS]
    line_part = f' | first-line: "{line}"' if line else ""
    return (
        f"[cluster {cluster_id}] {label} (size {size}) — "
        f"keywords: {kw_str}{line_part}"
    )


def _build_reduce_prompt(cluster_blocks: list[str], target_k: int) -> str:
    blocks = "\n".join(cluster_blocks)
    return (
        f"You are a documentation architect organizing topical clusters "
        f"into a coherent chapter outline.\n\n"
        f"Below are {len(cluster_blocks)} clusters from a software "
        f"framework's documentation. Merge them into a chapter outline. "
        f"Produce {_K_MIN}-{_K_MAX} chapters, ideally ~{target_k}; only "
        f"deviate from {target_k} if the material genuinely demands it. "
        f"Every cluster MUST appear in exactly one chapter. Order "
        f"chapters intro → core → advanced.\n\n"
        f"CLUSTERS:\n{blocks}\n\n"
        f"Respond ONLY with valid JSON matching this schema:\n"
        f"{{\n"
        f'  "chapters": [\n'
        f'    {{"title": "<2-6 word Title Case noun phrase>", '
        f'"description": "<1 sentence>", '
        f'"member_cluster_ids": [<int>, ...], '
        f'"order": <1-based int>}},\n'
        f'    ...\n'
        f'  ],\n'
        f'  "assigned_cluster_ids": [<every cluster id used, sorted ascending>]\n'
        f"}}\n\n"
        f"Hard rules:\n"
        f"- Total chapters: between {_K_MIN} and {_K_MAX}.\n"
        f"- Coverage: every input cluster_id appears in EXACTLY ONE chapter "
        f"AND in `assigned_cluster_ids`.\n"
        f"- Chapter titles must be specific, 2-6 words, Title Case, "
        f"noun phrase (NOT verbs like 'Getting Started').\n"
        f"- Descriptions: 1 sentence summarizing what the chapter covers.\n"
        f"- If a noise/'Unclustered' cluster exists, merge into the most "
        f"thematically-close chapter; only keep as standalone "
        f'"{_MISC_CHAPTER_TITLE}" chapter when it has substantial unique '
        f"content.\n"
        f'Bad chapter titles (avoid): "Various Topics", "Information", '
        f'"Documentation", "Misc".\n'
        f'Good chapter titles: "Observability and Tracing", '
        f'"Agent Orchestration", "Authentication and Security".'
    )


def _build_usc_vote_prompt(
    samples: list[dict], input_cluster_ids: set[int],
) -> str:
    lines = []
    for i, s in enumerate(samples):
        chapters = (s or {}).get("chapters") or []
        title_list = "; ".join(
            f"{(c or {}).get('title', '?')} "
            f"({len((c or {}).get('member_cluster_ids') or [])} clusters)"
            for c in chapters
        )
        missing, dupes, unknown = _validate_outline(s, input_cluster_ids)
        coverage = (
            f"missing={len(missing)}, dup={len(dupes)}, "
            f"unknown={len(unknown)}"
        )
        lines.append(
            f"[{i}] {len(chapters)} chapters · coverage [{coverage}] · "
            f"{title_list}"
        )
    rubric = "\n".join(lines)
    return (
        "You are reviewing candidate chapter outlines for a documentation "
        "framework. From the following candidates, pick the SINGLE BEST "
        "one — best coverage (no missing/duplicate clusters), best "
        "coherence (related clusters grouped), best chapter naming.\n\n"
        f"Candidates:\n{rubric}\n\n"
        'Respond ONLY with valid JSON: {"chosen_index": <int>}'
    )


def _build_refine_feedback_prompt(
    outline: dict, input_cluster_ids: set[int],
) -> str:
    chapters = (outline or {}).get("chapters") or []
    title_list = "\n".join(
        f"- [order {(c or {}).get('order', '?')}] "
        f"{(c or {}).get('title', '?')}: "
        f"clusters {(c or {}).get('member_cluster_ids') or []} — "
        f"{(c or {}).get('description', '')}"
        for c in chapters
    )
    missing, dupes, unknown = _validate_outline(outline, input_cluster_ids)
    coverage = (
        f"missing IDs: {missing[:10]}; duplicate IDs: {dupes[:10]}; "
        f"unknown IDs: {unknown[:10]}"
    )
    return (
        "Review this chapter outline. Identify SPECIFIC problems:\n"
        "(a) any 2+ chapters that overlap >50% conceptually\n"
        "(b) any chapter holding clusters that don't belong together\n"
        "(c) ordering coherence (intro → core → advanced)\n"
        "(d) coverage issues (missing/duplicate/unknown cluster IDs)\n\n"
        f"Current outline:\n{title_list}\n\n"
        f"Coverage: {coverage}\n\n"
        "Return a concise list of ≤5 issues to fix (or 'no issues' if "
        "the outline is already good). 1 line per issue. Plain text, "
        "no JSON."
    )


def _build_refine_apply_prompt(
    outline: dict, feedback: str, cluster_blocks: list[str], target_k: int,
) -> str:
    blocks = "\n".join(cluster_blocks)
    return (
        f"Apply this feedback to improve the chapter outline. Keep the "
        f"same JSON schema; preserve good chapters; only change what "
        f"the feedback identifies as broken. Produce {_K_MIN}-{_K_MAX} "
        f"chapters, soft-target ~{target_k}.\n\n"
        f"CLUSTERS (full context):\n{blocks}\n\n"
        f"CURRENT OUTLINE:\n{json.dumps(outline, indent=2)}\n\n"
        f"FEEDBACK:\n{feedback}\n\n"
        f"Respond ONLY with valid JSON matching the original schema "
        f"(chapters + assigned_cluster_ids)."
    )


def _build_coverage_repair_prompt(
    outline: dict, missing: list[int], duplicates: list[int],
    unknown: list[int], cluster_blocks: list[str],
) -> str:
    blocks = "\n".join(cluster_blocks)
    issues = []
    if missing:
        issues.append(
            f"- {len(missing)} clusters are MISSING from the outline: "
            f"{missing}"
        )
    if duplicates:
        issues.append(
            f"- {len(duplicates)} clusters appear in MULTIPLE chapters: "
            f"{duplicates}"
        )
    if unknown:
        issues.append(
            f"- {len(unknown)} cluster IDs in the outline DO NOT EXIST in "
            f"the input: {unknown}"
        )
    issues_text = "\n".join(issues)
    return (
        f"Fix coverage issues in this chapter outline. EVERY cluster must "
        f"appear in EXACTLY ONE chapter. Only assign clusters that exist "
        f"in the input.\n\n"
        f"CLUSTERS (full context, only these IDs are valid):\n{blocks}\n\n"
        f"CURRENT OUTLINE:\n{json.dumps(outline, indent=2)}\n\n"
        f"ISSUES TO FIX:\n{issues_text}\n\n"
        f"Respond ONLY with valid JSON matching the original schema "
        f"(chapters + assigned_cluster_ids). Keep good chapters, only "
        f"modify what's needed to fix the coverage issues."
    )


async def _generate_one_outline(prompt: str) -> tuple[dict | None, dict]:
    try:
        response, meta = await chat_judge_bandit_async(
            prompt, max_tokens=_MAX_TOKENS_OUTLINE,
            temperature=_TEMPERATURE,
        )
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    parsed = _parse_response(response)
    if not parsed:
        return None, {**meta, "error": "parse_failed",
                      "raw": (response or "")[:120]}
    return parsed, {**meta}


def _force_coverage_fallback(
    outline: dict, missing: list[int], duplicates: list[int],
    unknown: list[int],
) -> dict:
    """Last-resort: dump missing into Miscellaneous, dedupe duplicates
    (keep first chapter's claim), drop unknowns. Called only after
    _MAX_REPAIR_RETRIES exhausted."""
    chapters = list((outline or {}).get("chapters") or [])
    seen_ids: set[int] = set()
    unknown_set = set(unknown)
    for ch in chapters:
        if not isinstance(ch, dict):
            continue
        deduped = []
        for cid in (ch.get("member_cluster_ids") or []):
            try:
                cid_int = int(cid)
            except (TypeError, ValueError):
                continue
            if cid_int in unknown_set or cid_int in seen_ids:
                continue
            seen_ids.add(cid_int)
            deduped.append(cid_int)
        ch["member_cluster_ids"] = deduped
    chapters = [c for c in chapters if c.get("member_cluster_ids")]
    if missing:
        chapters.append({
            "title":              _MISC_CHAPTER_TITLE,
            "description":        (
                "Topics not assigned during automated chapter merging."
            ),
            "member_cluster_ids": list(missing),
            "order":              len(chapters) + 1,
        })
    for i, ch in enumerate(chapters, start=1):
        ch["order"] = i
    return {
        "chapters": chapters,
        "assigned_cluster_ids": sorted(
            cid for ch in chapters
            for cid in (ch.get("member_cluster_ids") or [])
        ),
    }


@traced("reduce")
async def reduce_node(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    cluster_ref = state.get("cluster_assignments_ref") or ""
    refine_ref = state.get("refine_assignments_ref") or ""
    labels_ref = state.get("cluster_labels_ref") or ""
    if not slug or not cluster_ref or not refine_ref or not labels_ref:
        return {
            "chapter_plan_ref": "",
            "reduce_stats": {"skipped": "no_input", "wall_ms": 0,
                             "n_chapters": 0},
        }

    t0 = time.monotonic()

    # ── Cache fast-path ────────────────────────────────────────────────
    mh = sha256(
        (f"cluster={cluster_ref}|refine={refine_ref}|"
         f"labels={labels_ref}|v={_PROMPT_VERSION}|"
         f"k={_TARGET_K}|min={_K_MIN}|max={_K_MAX}|"
         f"n={_N_SAMPLES}").encode("utf-8"),
    ).hexdigest()[:16]
    blob_key = _blob_key(slug, mh)
    minio = get_storage()

    if await minio.exists(blob_key):
        try:
            blob = await minio.read_text(blob_key)
            cached = json.loads(blob)
            outline = cached.get("outline") or {}
            chapters = outline.get("chapters") or []
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_chapters":     len(chapters),
                "n_clusters_in":  cached.get("n_clusters_in", 0),
                "n_repairs":      cached.get("n_repairs", 0),
                "wall_ms":        elapsed,
                "store_path":     blob_key,
                "cache_hit":      True,
                "outline":        outline,
                "prompt_version": cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "reduce", "done",
                n_chapters=len(chapters), wall_ms=elapsed, cache_hit=True,
            )
            logger.info(
                f"[reduce] {slug}: CACHE HIT — {len(chapters)} chapters, "
                f"{elapsed} ms"
            )
            return {"chapter_plan_ref": blob_key, "reduce_stats": stats}
        except Exception as e:
            logger.warning(
                f"[reduce] {slug}: cached blob {blob_key!r} unreadable "
                f"({type(e).__name__}: {e}); recomputing"
            )

    await emit_progress(thread_id, "reduce", "start")

    # ── Load upstream artifacts ────────────────────────────────────────
    cluster_blob = await minio.read_bytes(cluster_ref)
    cluster_keys, _orig, _max_probs, soft = load_clusters(cluster_blob)
    refine_blob = await minio.read_bytes(refine_ref)
    _, refined_assignments, _, _ = load_refine(refine_blob)
    labels_text = await minio.read_text(labels_ref)
    labels = load_labels(labels_text)

    bodies = await minio.read_many(cluster_keys)
    unique_clusters = sorted({
        int(cid) for cid in refined_assignments if int(cid) >= 0
    })
    n_clusters_in = len(unique_clusters)

    if n_clusters_in == 0:
        elapsed = int((time.monotonic() - t0) * 1000)
        outline: dict = {
            "chapters": [{
                "title": _MISC_CHAPTER_TITLE,
                "description": "Corpus did not yield any topical clusters.",
                "member_cluster_ids": [],
                "order": 1,
            }],
            "assigned_cluster_ids": [],
        }
        payload = {
            "outline":        outline,
            "n_clusters_in":  0,
            "n_repairs":      0,
            "prompt_version": _PROMPT_VERSION,
        }
        await minio.write(
            blob_key, json.dumps(payload), content_type="application/json",
        )
        stats = {
            "n_chapters": 1, "n_clusters_in": 0, "n_repairs": 0,
            "wall_ms": elapsed, "store_path": blob_key, "cache_hit": False,
            "outline": outline, "skipped": "no_clusters",
        }
        await emit_progress(
            thread_id, "reduce", "done",
            n_chapters=1, wall_ms=elapsed,
        )
        return {"chapter_plan_ref": blob_key, "reduce_stats": stats}

    # ── Per-cluster context (label + size + keywords + rep first line) ─
    cluster_docs_text: dict[int, str] = {}
    cluster_sizes: dict[int, int] = {}
    for cid in unique_clusters:
        cluster_mask_c = refined_assignments == cid
        idxs = np.where(cluster_mask_c)[0]
        cluster_sizes[cid] = int(len(idxs))
        if len(idxs):
            cluster_docs_text[cid] = " ".join(
                (bodies[int(i)] or "")[:_CTFIDF_DOC_CHARS] for i in idxs
            )
    cluster_keywords = _compute_cluster_keywords(
        cluster_docs_text, top_k=_KEYWORDS_PER_CLUSTER,
    )
    cluster_rep_lines = {
        cid: _pick_rep_first_line(cid, refined_assignments, soft, bodies)
        for cid in unique_clusters
    }
    cluster_blocks = [
        _build_cluster_context_block(
            cid,
            labels.get(cid, f"Cluster {cid}"),
            cluster_sizes.get(cid, 0),
            cluster_keywords.get(cid, []),
            cluster_rep_lines.get(cid, ""),
        )
        for cid in unique_clusters
    ]
    input_cluster_ids = set(unique_clusters)

    await emit_progress(
        thread_id, "reduce", "context_prepared",
        n_clusters_in=n_clusters_in,
    )

    # ── Generate N samples in parallel ─────────────────────────────────
    prompt = _build_reduce_prompt(cluster_blocks, _TARGET_K)
    sample_results = await asyncio.gather(*[
        _generate_one_outline(prompt) for _ in range(_N_SAMPLES)
    ])
    valid_samples = [
        (s, m) for s, m in sample_results if s is not None
    ]
    if not valid_samples:
        elapsed = int((time.monotonic() - t0) * 1000)
        outline = _force_coverage_fallback(
            {"chapters": []}, list(input_cluster_ids), [], [],
        )
        payload = {
            "outline": outline, "n_clusters_in": n_clusters_in,
            "n_repairs": 0, "prompt_version": _PROMPT_VERSION,
            "error": "all_samples_failed",
        }
        await minio.write(
            blob_key, json.dumps(payload), content_type="application/json",
        )
        stats = {
            "n_chapters": len(outline["chapters"]),
            "n_clusters_in": n_clusters_in, "n_repairs": 0,
            "wall_ms": elapsed, "store_path": blob_key, "cache_hit": False,
            "outline": outline, "error": "all_samples_failed",
        }
        await emit_progress(
            thread_id, "reduce", "done",
            n_chapters=len(outline["chapters"]), wall_ms=elapsed,
            error="all_samples_failed",
        )
        logger.warning(
            f"[reduce] {slug}: all {_N_SAMPLES} samples failed; "
            f"emitted fallback outline"
        )
        return {"chapter_plan_ref": blob_key, "reduce_stats": stats}

    await emit_progress(
        thread_id, "reduce", "samples_generated",
        n_samples=len(valid_samples),
    )

    # ── USC vote: pick best sample ─────────────────────────────────────
    chosen_sample = valid_samples[0][0]
    if len(valid_samples) > 1:
        vote_prompt = _build_usc_vote_prompt(
            [s for s, _ in valid_samples], input_cluster_ids,
        )
        try:
            vote_response, _ = await chat_judge_bandit_async(
                vote_prompt, max_tokens=_MAX_TOKENS_VOTE, temperature=0.0,
            )
            vote_parsed = _parse_response(vote_response)
            if vote_parsed and "chosen_index" in vote_parsed:
                idx = int(vote_parsed["chosen_index"])
                if 0 <= idx < len(valid_samples):
                    chosen_sample = valid_samples[idx][0]
        except Exception:
            pass

    await emit_progress(thread_id, "reduce", "usc_voted")

    # ── Self-refine pass (single round per Nature 2025 plateau) ────────
    feedback_prompt = _build_refine_feedback_prompt(
        chosen_sample, input_cluster_ids,
    )
    refined_outline = chosen_sample
    try:
        feedback_text, _ = await chat_judge_bandit_async(
            feedback_prompt, max_tokens=_MAX_TOKENS_VOTE, temperature=0.0,
        )
        if (
            feedback_text
            and "no issues" not in feedback_text.lower()
        ):
            apply_prompt = _build_refine_apply_prompt(
                chosen_sample, feedback_text, cluster_blocks, _TARGET_K,
            )
            apply_text, _ = await chat_judge_bandit_async(
                apply_prompt, max_tokens=_MAX_TOKENS_REFINE,
                temperature=_TEMPERATURE,
            )
            apply_parsed = _parse_response(apply_text)
            if apply_parsed and apply_parsed.get("chapters"):
                refined_outline = apply_parsed
    except Exception as e:
        logger.warning(
            f"[reduce] {slug}: self-refine pass failed "
            f"({type(e).__name__}: {e}); using USC winner as-is"
        )

    await emit_progress(thread_id, "reduce", "refined")

    # ── Coverage validation + repair retries ───────────────────────────
    n_repairs = 0
    for attempt in range(_MAX_REPAIR_RETRIES):
        missing, dupes, unknown = _validate_outline(
            refined_outline, input_cluster_ids,
        )
        if not (missing or dupes or unknown):
            break
        n_repairs += 1
        await emit_progress(
            thread_id, "reduce", "repair_attempt",
            attempt=attempt + 1, missing=len(missing),
            duplicate=len(dupes), unknown=len(unknown),
        )
        repair_prompt = _build_coverage_repair_prompt(
            refined_outline, missing, dupes, unknown, cluster_blocks,
        )
        try:
            repair_text, _ = await chat_judge_bandit_async(
                repair_prompt, max_tokens=_MAX_TOKENS_REPAIR,
                temperature=0.0,
            )
            repair_parsed = _parse_response(repair_text)
            if repair_parsed and repair_parsed.get("chapters"):
                refined_outline = repair_parsed
        except Exception as e:
            logger.warning(
                f"[reduce] {slug}: repair attempt {attempt + 1} failed "
                f"({type(e).__name__}: {e})"
            )
            break

    # Final fallback: if still incomplete after retries, force-repair.
    missing, dupes, unknown = _validate_outline(
        refined_outline, input_cluster_ids,
    )
    forced_repair = False
    if missing or dupes or unknown:
        refined_outline = _force_coverage_fallback(
            refined_outline, missing, dupes, unknown,
        )
        forced_repair = True
        logger.warning(
            f"[reduce] {slug}: coverage incomplete after "
            f"{_MAX_REPAIR_RETRIES} retries; force-repair applied "
            f"(missing={len(missing)}, dup={len(dupes)}, "
            f"unknown={len(unknown)})"
        )

    # ── Persist + return ───────────────────────────────────────────────
    chapters = refined_outline.get("chapters") or []
    payload = {
        "outline":        refined_outline,
        "n_clusters_in":  n_clusters_in,
        "n_repairs":      n_repairs,
        "forced_repair":  forced_repair,
        "prompt_version": _PROMPT_VERSION,
    }
    await minio.write(
        blob_key, json.dumps(payload), content_type="application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_chapters":     len(chapters),
        "n_clusters_in":  n_clusters_in,
        "n_samples":      len(valid_samples),
        "n_repairs":      n_repairs,
        "forced_repair":  forced_repair,
        "wall_ms":        elapsed,
        "store_path":     blob_key,
        "cache_hit":      False,
        "outline":        refined_outline,
        "prompt_version": _PROMPT_VERSION,
    }
    await emit_progress(
        thread_id, "reduce", "done",
        n_chapters=len(chapters), n_repairs=n_repairs,
        forced_repair=forced_repair, wall_ms=elapsed,
    )
    logger.info(
        f"[reduce] {slug}: {len(chapters)} chapters from {n_clusters_in} "
        f"clusters; {n_repairs} repairs"
        f"{' (forced)' if forced_repair else ''}; {elapsed} ms"
    )
    return {"chapter_plan_ref": blob_key, "reduce_stats": stats}


def load_outline(text: str) -> dict:
    """Convenience loader for downstream nodes (validate, plan_write)."""
    payload = json.loads(text)
    return payload.get("outline") or {}
