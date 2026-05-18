"""Substep 7 — label: cluster naming via bandit-routed big-LLM.

Per `docs/PLANNER-ARCHITECTURE-2026-05-17.md` + May 2026 SOTA research
(BERTopic-LLM defaults + TopicGPT arXiv 2311.01449 + Tutmaier 2025
arXiv 2502.18469 + Universal Self-Consistency arXiv 2311.17311 + LiSA
ACL 2025). Pipeline:

  1. Read cluster's soft-membership matrix + refine's reassigned
     cluster IDs.
  2. For each refined cluster, compute:
     - Top-20 c-TF-IDF keywords (reuse refine.py's helper)
     - Top-8 representative doc snippets (highest in-cluster soft
       membership; first 500 chars per doc)
  3. ROUND 1 — blind labeling: per-cluster prompt with keywords + rep
     docs, NO sibling labels. N=3 samples per cluster at temp=0.3,
     then Universal Self-Consistency vote (1 extra LLM call) picks
     the best. Unanimous samples skip USC.
  4. ROUND 2 — sibling-aware re-labeling: any cluster whose USC vote
     was NOT unanimous gets re-labeled with all round-1 labels in
     the "Existing labels in this corpus (DO NOT duplicate)" block.
  5. Noise cluster (-1) gets a hardcoded "Unclustered" — NEVER ask
     the LLM to name noise (Tutmaier 2025: hallucinated coherence).
  6. Persist labels + per-cluster decisions to MinIO as JSON.

State writes:
  cluster_labels_ref — MinIO key of the labels JSON blob
  label_stats        — observability dict (counts + bandit telemetry +
                       full label map for the UI)

Why these knobs (research-backed):
- Temp=0.3 (not 0): siblings collide on generic labels at temp=0
  (Tutmaier 2025, Stochastic Sandbox 2026).
- 8 rep docs (not 4): quality-over-speed sweet spot (Tutmaier 2025
  Approach 3 winner; BERTopic default is 4).
- Top-N by centroid (not diversity sampling): Tutmaier 2025 Approach
  4 consistently underperformed.
- First 500 chars per doc (not random middle): intro paragraph is the
  highest signal-density region for documentation pages.
- "Existing labels" block in round 2: MIT 2025 grammar-pattern study
  shows LLMs collide on shallow lexical patterns when blind to context.
- NOT batched ("label all 19 at once"): blows context window; quality
  drops past ~30K input on free-tier rotators (per research agent
  May 2026 brief).
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
from .refine import _compute_cluster_keywords, load_refine


logger = logging.getLogger(__name__)


# USC sample count — N=3 is the sweet spot per Wang 2025 / Chen 2023.
_N_SAMPLES = 3
# Representative docs per cluster — Tutmaier 2025 Approach 3 sweet spot.
_REP_DOCS_PER_CLUSTER = 8
# First-N chars per rep doc. Doc intros are highest signal density.
_REP_DOC_CHARS = 500
# c-TF-IDF keyword count per cluster (top distinctive terms).
_KEYWORDS_TOP_K = 20
# Per-call LLM budget. JSON + 1-sentence rationale + 2 alternates fits.
_MAX_TOKENS = 120
# Mild temperature — temp=0 causes sibling clusters to collide on
# generic labels like "Configuration" twice (research-confirmed).
_TEMPERATURE = 0.3
# Parallel cluster-label calls.
_CONCURRENCY = 8
# c-TF-IDF doc-text cap (matches refine.py for cross-step consistency).
_CTFIDF_DOC_CHARS = 1200
# Cache version — bump on prompt redesign so old blobs invalidate.
_PROMPT_VERSION = "v1-2026-05-18"
_BLOB_PREFIX = "planner"

# Hardcoded label for the HDBSCAN noise cluster (-1). NEVER ask the
# LLM to name noise — Tutmaier 2025 found it hallucinates coherence.
_NOISE_LABEL = "Unclustered"


def _blob_key(slug: str, manifest_hash: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/labels/{manifest_hash}.json"


def _pick_top_n_rep_docs(
    cluster_id: int,
    refined_assignments: np.ndarray,
    soft: np.ndarray,
    bodies: list[str],
    n: int = _REP_DOCS_PER_CLUSTER,
) -> list[str]:
    """Top-N docs in the refined cluster by soft_membership[:, cluster_id].
    Refined-cluster docs might include reassignments from refine; we use
    the original soft matrix's column for that cluster as the proximity
    score. Out-of-range cluster_id (e.g. noise -1) returns first-N
    members of the cluster as fallback."""
    cluster_mask = refined_assignments == cluster_id
    if not cluster_mask.any():
        return []
    if cluster_id < 0 or cluster_id >= soft.shape[1]:
        idxs = np.where(cluster_mask)[0][:n]
        return [(bodies[i] or "")[:_REP_DOC_CHARS] for i in idxs]
    membership = soft[:, cluster_id]
    masked = np.where(cluster_mask, membership, -np.inf)
    top_idx = np.argsort(-masked)[:n]
    return [
        (bodies[int(i)] or "")[:_REP_DOC_CHARS]
        for i in top_idx if cluster_mask[int(i)]
    ]


def _build_label_prompt(
    keywords: list[str],
    rep_docs: list[str],
    existing_labels: list[str],
) -> str:
    """Per-cluster prompt: SYSTEM persona + label constraints + USER
    context (keywords + rep docs + optional sibling labels). Sibling
    block is empty on round 1, populated on round 2."""
    existing_block = ""
    if existing_labels:
        existing_block = (
            "Existing labels in this corpus (DO NOT duplicate; "
            "differentiate against these):\n" +
            "\n".join(f"- {l}" for l in existing_labels) + "\n\n"
        )
    kw_str = ", ".join(keywords[:_KEYWORDS_TOP_K]) or "(no keywords)"
    doc_block = "\n".join(
        f"[{i + 1}] {(d or '').strip()[:_REP_DOC_CHARS]}"
        for i, d in enumerate(rep_docs[:_REP_DOCS_PER_CLUSTER])
    ) or "(no representative docs)"
    return (
        "You are a documentation taxonomist. Given a cluster of related "
        "documentation pages, produce a concise chapter-style label "
        "(2-4 words, Title Case, noun phrase). The label must be specific "
        "enough to distinguish from sibling clusters, but general enough "
        "to cover the whole cluster.\n\n"
        "This cluster contains pages about a software framework. Below "
        "are its most distinctive keywords (c-TF-IDF) and representative "
        "document snippets.\n\n" +
        existing_block +
        f"Cluster keywords (top {_KEYWORDS_TOP_K}, c-TF-IDF):\n"
        f"{kw_str}\n\n"
        f"Representative documents (top {_REP_DOCS_PER_CLUSTER}, ranked "
        f"by proximity to cluster centroid):\n"
        f"{doc_block}\n\n"
        "Respond ONLY with valid JSON: "
        '{"label": "<2-4 word Title Case noun phrase>", '
        '"rationale": "<1 sentence why this label fits>", '
        '"alternates": ["<alt1>", "<alt2>"]}\n\n'
        'Bad labels (avoid): "Information", "Documentation", '
        '"Various Topics", "Misc".\n'
        'Good labels: "Authentication & Authorization", '
        '"Streaming Responses", "Tool Use", "Prompt Caching".'
    )


_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)
# Strip wrappers the LM may emit despite the prompt — markdown emphasis,
# "Title:" / "Chapter N:" / "Label:" prefixes, leading punctuation runs.
# Same pattern as v1's classical_map.py (proven battle-tested).
_LEADING_LABEL_RE = re.compile(
    r"^\s*"
    r"(?:\*+\s*)?"
    r"(?:chapter\s+\d+\s*[:\-.]?\s*)?"
    r"(?:(?:title|label|topic|name|cluster)\s*[:\-]\s*)?"
    r"[:*\-.]*\s*",
    re.IGNORECASE,
)


def _sanitize_label(label: str) -> str:
    """Strip wrappers + leading prefix words + outer quotes/emphasis."""
    if not label:
        return ""
    s = label.strip().strip('"').strip("'").strip("*").strip()
    s = _LEADING_LABEL_RE.sub("", s).strip()
    s = s.strip('"').strip("'").strip().strip("*").strip()
    return s


def _parse_response(text: str) -> dict | None:
    """Best-effort JSON extraction (same shape as refine.py)."""
    if not text:
        return None
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def _label_one_sample(
    sem: asyncio.Semaphore, prompt: str,
) -> tuple[str | None, dict]:
    """One LLM call. Returns (sanitized_label_or_None, meta)."""
    async with sem:
        try:
            response, meta = await chat_judge_bandit_async(
                prompt, max_tokens=_MAX_TOKENS, temperature=_TEMPERATURE,
            )
        except Exception as e:
            return None, {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    parsed = _parse_response(response)
    if not parsed:
        return None, {**meta, "error": "parse_failed",
                      "raw": (response or "")[:80]}
    raw_label = parsed.get("label") or ""
    label = _sanitize_label(raw_label)
    if not label:
        return None, {**meta, "error": "empty_label",
                      "raw": (response or "")[:80]}
    return label, {
        **meta,
        "rationale":  parsed.get("rationale"),
        "alternates": parsed.get("alternates") or [],
    }


async def _label_one_cluster_usc(
    sem: asyncio.Semaphore,
    cluster_id: int,
    prompt: str,
    n_samples: int = _N_SAMPLES,
) -> dict:
    """N samples + Universal Self-Consistency vote.
    Unanimous samples skip the USC call. Returns the verdict dict."""
    sample_results = await asyncio.gather(*[
        _label_one_sample(sem, prompt) for _ in range(n_samples)
    ])
    labels = [r[0] for r in sample_results if r[0]]
    metas = [r[1] for r in sample_results]
    if not labels:
        return {
            "cluster_id": cluster_id, "label": _NOISE_LABEL,
            "usc_vote": "no_valid_samples", "samples": [], "metas": metas,
            "error": "all_samples_failed",
        }
    unique_labels = list(dict.fromkeys(labels))   # preserve order, dedupe
    if len(unique_labels) == 1:
        return {
            "cluster_id": cluster_id, "label": unique_labels[0],
            "usc_vote": "unanimous", "samples": labels, "metas": metas,
            "error": None,
        }
    # USC: 1 extra LLM call to pick the best of the candidates.
    usc_prompt = (
        "You are reviewing candidate labels for a documentation cluster. "
        "From the following candidates, pick the single best one — "
        "specific, concise, 2-4 words, Title Case noun phrase.\n\n"
        "Candidates:\n" +
        "\n".join(f"- {l}" for l in unique_labels) +
        '\n\nRespond ONLY with valid JSON: {"label": "<chosen label>"}'
    )
    final_label, final_meta = await _label_one_sample(sem, usc_prompt)
    if not final_label:
        # Fallback: most-common among the original samples.
        final_label = max(set(labels), key=labels.count)
        return {
            "cluster_id": cluster_id, "label": final_label,
            "usc_vote": "mode_fallback", "samples": labels,
            "metas": metas + [final_meta],
            "error": (final_meta or {}).get("error"),
        }
    return {
        "cluster_id": cluster_id, "label": final_label,
        "usc_vote": "usc_voted", "samples": labels,
        "metas": metas + [final_meta],
        "error": None,
    }


@traced("label")
async def label(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    cluster_ref = state.get("cluster_assignments_ref") or ""
    refine_ref = state.get("refine_assignments_ref") or ""
    if not slug or not cluster_ref or not refine_ref:
        return {
            "cluster_labels_ref": "",
            "label_stats": {"skipped": "no_input", "wall_ms": 0,
                            "n_clusters": 0},
        }

    t0 = time.monotonic()

    # ── Cache fast-path ────────────────────────────────────────────────
    mh = sha256(
        (f"cluster={cluster_ref}|refine={refine_ref}|"
         f"v={_PROMPT_VERSION}|n={_N_SAMPLES}|"
         f"reps={_REP_DOCS_PER_CLUSTER}|"
         f"kw={_KEYWORDS_TOP_K}").encode("utf-8"),
    ).hexdigest()[:16]
    blob_key = _blob_key(slug, mh)
    minio = get_storage()

    if await minio.exists(blob_key):
        try:
            blob = await minio.read_text(blob_key)
            cached = json.loads(blob)
            labels_dict = cached.get("labels") or {}
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_clusters":   len(labels_dict) - (
                    1 if "-1" in labels_dict else 0
                ),
                "n_round2":     cached.get("n_round2", 0),
                "wall_ms":      elapsed,
                "store_path":   blob_key,
                "cache_hit":    True,
                "n_samples":    _N_SAMPLES,
                "labels":       labels_dict,
                "prompt_version": cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "label", "done",
                n_clusters=stats["n_clusters"], n_round2=stats["n_round2"],
                wall_ms=elapsed, cache_hit=True,
            )
            logger.info(
                f"[label] {slug}: CACHE HIT — {stats['n_clusters']} labels, "
                f"{elapsed} ms"
            )
            return {"cluster_labels_ref": blob_key, "label_stats": stats}
        except Exception as e:
            logger.warning(
                f"[label] {slug}: cached blob {blob_key!r} unreadable "
                f"({type(e).__name__}: {e}); recomputing"
            )

    await emit_progress(thread_id, "label", "start")

    # ── Load cluster + refine artifacts ────────────────────────────────
    cluster_blob = await minio.read_bytes(cluster_ref)
    cluster_keys, _orig_assigns, _max_probs, soft = load_clusters(cluster_blob)
    refine_blob = await minio.read_bytes(refine_ref)
    refine_keys, refined_assignments, _, _ = load_refine(refine_blob)

    if cluster_keys != refine_keys:
        raise RuntimeError(
            f"label: key mismatch — cluster has {len(cluster_keys)} keys, "
            f"refine has {len(refine_keys)}; pipeline integrity broken"
        )

    bodies = await minio.read_many(cluster_keys)
    unique_clusters = sorted({
        int(cid) for cid in refined_assignments if int(cid) >= 0
    })
    n_clusters = len(unique_clusters)

    if n_clusters == 0:
        elapsed = int((time.monotonic() - t0) * 1000)
        payload = {
            "labels": {"-1": _NOISE_LABEL},
            "n_round2": 0,
            "prompt_version": _PROMPT_VERSION,
            "round1_decisions": {},
        }
        await minio.write(
            blob_key, json.dumps(payload), content_type="application/json",
        )
        stats = {
            "n_clusters":   0, "n_round2": 0, "wall_ms": elapsed,
            "store_path":   blob_key, "cache_hit": False,
            "skipped":      "no_clusters", "n_samples": _N_SAMPLES,
            "labels":       {"-1": _NOISE_LABEL},
        }
        await emit_progress(
            thread_id, "label", "done",
            n_clusters=0, n_round2=0, wall_ms=elapsed,
        )
        return {"cluster_labels_ref": blob_key, "label_stats": stats}

    # ── Per-cluster c-TF-IDF keywords + rep docs ───────────────────────
    cluster_docs_text: dict[int, str] = {}
    for cid in unique_clusters:
        cluster_mask_c = refined_assignments == cid
        idxs = np.where(cluster_mask_c)[0]
        if not len(idxs):
            continue
        cluster_docs_text[cid] = " ".join(
            (bodies[int(i)] or "")[:_CTFIDF_DOC_CHARS] for i in idxs
        )
    cluster_keywords = _compute_cluster_keywords(
        cluster_docs_text, top_k=_KEYWORDS_TOP_K,
    )
    cluster_rep_docs = {
        cid: _pick_top_n_rep_docs(
            cid, refined_assignments, soft, bodies,
            n=_REP_DOCS_PER_CLUSTER,
        )
        for cid in unique_clusters
    }

    await emit_progress(
        thread_id, "label", "context_prepared",
        n_clusters=n_clusters,
    )

    sem = asyncio.Semaphore(_CONCURRENCY)

    # ── Round 1: blind labeling (no sibling-aware context) ─────────────
    judged_done = {"n": 0, "unanimous": 0, "usc": 0, "err": 0,
                   "round": "round1"}
    _EMIT_EVERY = max(1, n_clusters // 20)

    async def _track_label(cid: int, existing: list[str]) -> dict:
        prompt = _build_label_prompt(
            cluster_keywords.get(cid, []),
            cluster_rep_docs.get(cid, []),
            existing,
        )
        result = await _label_one_cluster_usc(sem, cid, prompt)
        judged_done["n"] += 1
        if result.get("error"):
            judged_done["err"] += 1
        elif result["usc_vote"] == "unanimous":
            judged_done["unanimous"] += 1
        else:
            judged_done["usc"] += 1
        if (
            judged_done["n"] % _EMIT_EVERY == 0
            or judged_done["n"] == n_clusters
        ):
            await emit_progress(
                thread_id, "label", "llm_progress",
                judged=judged_done["n"], total=n_clusters,
                unanimous=judged_done["unanimous"],
                usc=judged_done["usc"], err=judged_done["err"],
                round=judged_done["round"],
            )
        return result

    round1_tasks = [_track_label(cid, []) for cid in unique_clusters]
    round1_results = await asyncio.gather(*round1_tasks)

    labels: dict[int, str] = {}
    round1_decisions: dict[int, dict] = {}
    for r in round1_results:
        labels[r["cluster_id"]] = r["label"]
        round1_decisions[r["cluster_id"]] = r

    # ── Round 2: re-label USC-split clusters with sibling-aware context ─
    split_cids = [
        r["cluster_id"] for r in round1_results
        if r["usc_vote"] in (
            "usc_voted", "mode_fallback", "no_valid_samples",
        )
    ]
    n_round2 = 0
    if split_cids:
        judged_done["n"] = 0
        judged_done["unanimous"] = 0
        judged_done["usc"] = 0
        judged_done["err"] = 0
        judged_done["round"] = "round2"
        await emit_progress(
            thread_id, "label", "round2_start",
            n_round2=len(split_cids),
        )
        round2_tasks = []
        for cid in split_cids:
            existing = [v for k, v in labels.items() if k != cid]
            round2_tasks.append(_track_label(cid, existing))
        round2_results = await asyncio.gather(*round2_tasks)
        for r in round2_results:
            labels[r["cluster_id"]] = r["label"]
        n_round2 = len(round2_results)

    labels[-1] = _NOISE_LABEL

    # ── Persist to MinIO (JSON, labels are small dicts) ────────────────
    payload = {
        "labels": {str(k): v for k, v in labels.items()},
        "n_round2": n_round2,
        "prompt_version": _PROMPT_VERSION,
        "round1_decisions": {
            str(k): {
                "label":    v["label"],
                "usc_vote": v["usc_vote"],
                "samples":  v["samples"],
                "error":    v.get("error"),
            }
            for k, v in round1_decisions.items()
        },
    }
    await minio.write(
        blob_key, json.dumps(payload), content_type="application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    n_unanimous = sum(
        1 for r in round1_results if r["usc_vote"] == "unanimous"
    )
    n_usc_voted = sum(
        1 for r in round1_results if r["usc_vote"] == "usc_voted"
    )
    n_errors = sum(1 for r in round1_results if r.get("error"))

    # Bandit deployment-usage tally (which models actually answered)
    dep_usage: dict[str, int] = {}
    for r in round1_results:
        for m in (r.get("metas") or []):
            dep = (m or {}).get("deployment") or "?"
            dep_usage[dep] = dep_usage.get(dep, 0) + 1
    deployment_summary = [
        {"deployment": dep, "calls": n}
        for dep, n in sorted(dep_usage.items(), key=lambda kv: -kv[1])
    ]

    stats = {
        "n_clusters":       n_clusters,
        "n_unanimous":      n_unanimous,
        "n_usc_voted":      n_usc_voted,
        "n_round2":         n_round2,
        "n_errors":         n_errors,
        "wall_ms":          elapsed,
        "store_path":       blob_key,
        "cache_hit":        False,
        "n_samples":        _N_SAMPLES,
        "labels":           {str(k): v for k, v in labels.items()},
        "deployment_usage": deployment_summary,
        "prompt_version":   _PROMPT_VERSION,
    }

    await emit_progress(
        thread_id, "label", "done",
        n_clusters=n_clusters, n_round2=n_round2, wall_ms=elapsed,
    )
    logger.info(
        f"[label] {slug}: {n_clusters} clusters labeled, "
        f"{n_unanimous} unanimous, {n_usc_voted} USC-voted, "
        f"{n_round2} round-2 re-labels, {n_errors} errors; {elapsed} ms"
    )
    return {"cluster_labels_ref": blob_key, "label_stats": stats}


def load_labels(text: str) -> dict[int, str]:
    """Convenience loader for downstream nodes (reduce, validate)."""
    payload = json.loads(text)
    raw = payload.get("labels") or {}
    return {int(k): str(v) for k, v in raw.items()}
