"""Substep 6 — refine: LITA boundary-doc reassignment via bandit LLM.

Per `docs/PLANNER-ARCHITECTURE-2026-05-17.md` + May 2026 SOTA research
(LITA arXiv 2412.12459, "None of the Above" ACL 2025, position-bias
IJCNLP 2025, Wharton CoT 2025, k-LLMmeans arXiv 2502.09667). Pipeline:

  1. Read cluster's soft-membership matrix (N×K) + assignments.
  2. Identify boundary docs where max_prob < _BOUNDARY_FLOOR (0.60).
  3. Compute per-cluster context — top-7 c-TF-IDF keywords + 1
     representative-doc snippet (chosen as the doc with highest
     in-cluster soft membership).
  4. For each boundary doc: take the top-K=5 candidate clusters from
     the soft matrix, shuffle letter labels A-E (defeats primacy bias),
     build a strict JSON-output prompt, call the ParetoBandit-routed
     big-LLM via `chat_judge_bandit_async`, parse the verdict, map the
     letter back to the original cluster_id.
  5. Allow `null` response — boundary docs that fit no candidate stay
     as noise (-1). Per ACL 2025 NOTA paper: forcing a pick drops
     accuracy 30-50%.
  6. Persist refined assignments + per-doc decisions to MinIO.

State writes:
  refine_assignments_ref — MinIO key of the .npz blob
  refine_stats           — observability dict (counts + bandit telemetry)

The .npz holds: keys (N), refined_assignments (N), original_assignments
(N), decisions_json (list of dicts with doc_idx + verdict + meta).
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import random
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


logger = logging.getLogger(__name__)


# Boundary threshold — slightly more generous than cluster's 0.5 floor.
# Tokens are free per project policy; refine more docs.
_BOUNDARY_FLOOR = 0.60
# Top-K candidate clusters offered per boundary doc. Research: >10 hurts
# accuracy due to position bias + prompt length; top-5 is the sweet spot.
_TOP_K = 5
# c-TF-IDF keyword count per cluster — research: 5-8.
_KEYWORDS_PER_CLUSTER = 7
# Representative-doc snippet length per cluster (~80 tokens).
_SNIPPET_CHARS = 320
# Body of the doc being judged (truncated to bound prompt size, ~600 tokens).
_DOC_BODY_CHARS = 2400
# Per-cluster doc-text cap when building c-TF-IDF corpus (keeps TF-IDF fast).
_CTFIDF_DOC_CHARS = 1200
# Concurrency — 8 in-flight. ParetoBandit + LiteLLM cooldowns handle
# rate-limit pressure within the dd-all rotator.
_REFINE_CONCURRENCY = 8
# Per-call LLM budget for the JSON output (chosen, confidence, rationale).
_REFINE_MAX_TOKENS = 200
# Cache version — bump on prompt redesign / hyperparam tweaks so old
# blobs invalidate cleanly.
_PROMPT_VERSION = "v1-2026-05-18"
_BLOB_PREFIX = "planner"

# Letter labels A-E for the top-5 candidates.
_LABELS = list("ABCDE")


def _blob_key(slug: str, manifest_hash: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/refine/{manifest_hash}.npz"


def _pack_npz(
    keys: list[str],
    refined_assignments: np.ndarray,
    original_assignments: np.ndarray,
    decisions: list[dict],
) -> bytes:
    """Serialize refine artifacts to compressed .npz. decisions is a
    list of small dicts (doc_idx, new_cluster_id, confidence, rationale,
    deployment, latency_s, error) — stored as a single JSON string."""
    buf = io.BytesIO()
    np.savez_compressed(
        buf,
        keys=np.array(keys, dtype=object),
        refined_assignments=refined_assignments.astype(np.int32),
        original_assignments=original_assignments.astype(np.int32),
        decisions_json=np.array(json.dumps(decisions), dtype=object),
    )
    return buf.getvalue()


def load_refine(blob_bytes: bytes):
    """Inverse of _pack_npz. Used by label/reduce downstream."""
    buf = io.BytesIO(blob_bytes)
    with np.load(buf, allow_pickle=True) as data:
        keys = [str(k) for k in data["keys"].tolist()]
        refined = np.asarray(data["refined_assignments"], dtype=np.int32)
        original = np.asarray(data["original_assignments"], dtype=np.int32)
        decisions = json.loads(str(data["decisions_json"]))
    return keys, refined, original, decisions


def _compute_cluster_keywords(
    cluster_docs_text: dict[int, str],
    top_k: int = _KEYWORDS_PER_CLUSTER,
) -> dict[int, list[str]]:
    """c-TF-IDF (BERTopic-style) over per-cluster concatenated docs.
    Returns top-K distinctive uni+bigrams per cluster."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    cluster_ids = sorted(cluster_docs_text.keys())
    corpus = [cluster_docs_text[cid] for cid in cluster_ids]
    if not corpus:
        return {}
    vec = TfidfVectorizer(
        max_features=5000,
        ngram_range=(1, 2),
        stop_words="english",
        lowercase=True,
        min_df=1,
    )
    try:
        matrix = vec.fit_transform(corpus)
    except ValueError:
        # Empty vocab (all stopwords / empty corpus) — return empty.
        return {cid: [] for cid in cluster_ids}
    feature_names = vec.get_feature_names_out()
    keywords: dict[int, list[str]] = {}
    for i, cid in enumerate(cluster_ids):
        row = matrix[i].toarray().flatten()
        top_idx = np.argsort(row)[::-1][:top_k]
        keywords[cid] = [feature_names[j] for j in top_idx if row[j] > 0]
    return keywords


def _pick_representative_doc(
    cluster_id: int,
    assignments: np.ndarray,
    soft: np.ndarray,
    bodies: list[str],
) -> str:
    """Pick the in-cluster doc with the highest soft-membership for its
    own cluster. Returns truncated body."""
    cluster_mask = assignments == cluster_id
    if not cluster_mask.any():
        return ""
    if cluster_id < 0 or cluster_id >= soft.shape[1]:
        idxs = np.where(cluster_mask)[0]
        return (bodies[idxs[0]] or "")[:_SNIPPET_CHARS]
    membership = soft[:, cluster_id]
    masked = np.where(cluster_mask, membership, -np.inf)
    best_idx = int(np.argmax(masked))
    return (bodies[best_idx] or "")[:_SNIPPET_CHARS]


def _build_prompt(
    doc_body: str,
    candidates: list[tuple[str, list[str], str]],
) -> str:
    """Letter-labeled JSON-output prompt. Per 2026 SOTA research: top-5
    candidates max, shuffled letters defeat primacy bias, null allowed
    for "no fit" (force-pick drops accuracy 30-50% per ACL 2025), JSON
    output for parse reliability (no chain-of-thought — Wharton 2025
    showed CoT hurts text classification)."""
    cand_lines = []
    for letter, keywords, snippet in candidates:
        kw_str = ", ".join(keywords[:_KEYWORDS_PER_CLUSTER]) or "(no keywords)"
        snip = (snippet or "").strip().replace("\n", " ")[:_SNIPPET_CHARS]
        snip_part = f' | sample: "{snip}"' if snip else ""
        cand_lines.append(f"[{letter}] keywords: {kw_str}{snip_part}")
    cand_text = "\n".join(cand_lines)

    return (
        f"You assign documents to existing topic clusters. Below is one "
        f"document and {len(candidates)} candidate clusters (presented in "
        f"shuffled order). Pick the cluster whose theme best matches the "
        f"document, or return null if none fit.\n\n"
        f"DOCUMENT:\n"
        f"<<<{(doc_body or '').strip()[:_DOC_BODY_CHARS]}>>>\n\n"
        f"CANDIDATES:\n"
        f"{cand_text}\n\n"
        f'Respond ONLY with JSON: '
        f'{{"chosen_cluster_id": "A"|"B"|"C"|"D"|"E"|null, '
        f'"confidence": "high"|"medium"|"low", '
        f'"rationale": "<20 words why"}}'
    )


_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)


def _parse_response(text: str) -> dict | None:
    """Best-effort JSON extraction. Tries direct json.loads, then falls
    back to extracting the first JSON-shaped substring (handles models
    that wrap the JSON in prose despite the prompt)."""
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


async def _refine_one(
    sem: asyncio.Semaphore,
    doc_idx: int,
    body: str,
    candidate_cluster_ids: list[int],
    cluster_keywords: dict[int, list[str]],
    cluster_snippets: dict[int, str],
    original_cluster_id: int,
) -> dict:
    """One LLM call to refine a boundary doc's assignment."""
    if not candidate_cluster_ids:
        return {
            "doc_idx": doc_idx, "new_cluster_id": original_cluster_id,
            "confidence": "low", "rationale": "no candidates",
            "meta": {}, "error": "empty_candidates",
        }
    letters = _LABELS[:len(candidate_cluster_ids)]
    shuffled = letters[:]
    random.shuffle(shuffled)
    letter_to_cid = dict(zip(shuffled, candidate_cluster_ids))
    candidates_for_prompt = [
        (L, cluster_keywords.get(letter_to_cid[L], []),
         cluster_snippets.get(letter_to_cid[L], ""))
        for L in letters
    ]
    prompt = _build_prompt(body, candidates_for_prompt)

    async with sem:
        try:
            response, meta = await chat_judge_bandit_async(
                prompt, max_tokens=_REFINE_MAX_TOKENS, temperature=0.0,
            )
        except Exception as e:
            return {
                "doc_idx": doc_idx, "new_cluster_id": original_cluster_id,
                "confidence": "low",
                "rationale": "rotator failure — kept original",
                "meta": {}, "error": f"{type(e).__name__}: {str(e)[:120]}",
            }
    parsed = _parse_response(response)
    if not parsed:
        return {
            "doc_idx": doc_idx, "new_cluster_id": original_cluster_id,
            "confidence": "low", "rationale": "unparseable response",
            "meta": meta, "error": "parse_failed",
        }
    chosen = parsed.get("chosen_cluster_id")
    confidence = parsed.get("confidence") or "low"
    rationale = (parsed.get("rationale") or "")[:120]
    if chosen is None or (isinstance(chosen, str) and chosen.upper() == "NULL"):
        # "None of these" → noise label (LITA's NOTA case).
        return {
            "doc_idx": doc_idx, "new_cluster_id": -1,
            "confidence": confidence, "rationale": rationale,
            "meta": meta, "error": None,
        }
    chosen_letter = str(chosen).strip().upper().strip(".,;:!\"'`)")
    if chosen_letter not in letter_to_cid:
        # Invalid letter — fall back to top-1 candidate (highest soft prob).
        return {
            "doc_idx": doc_idx, "new_cluster_id": candidate_cluster_ids[0],
            "confidence": "low",
            "rationale": f"invalid letter: {chosen_letter}",
            "meta": meta, "error": "bad_letter",
        }
    return {
        "doc_idx": doc_idx,
        "new_cluster_id": int(letter_to_cid[chosen_letter]),
        "confidence": confidence, "rationale": rationale,
        "meta": meta, "error": None,
    }


@traced("refine")
async def refine(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    cluster_ref = state.get("cluster_assignments_ref") or ""
    if not slug or not cluster_ref:
        return {
            "refine_assignments_ref": "",
            "refine_stats": {"skipped": "no input", "n_docs": 0, "wall_ms": 0},
        }

    t0 = time.monotonic()

    # ── Cache fast-path ────────────────────────────────────────────────
    # Hash includes cluster_ref (itself content-addressed), threshold,
    # top-K, keyword count, prompt version. Any of these change → cache
    # invalidates cleanly.
    mh = sha256(
        (f"cluster={cluster_ref}|floor={_BOUNDARY_FLOOR}|"
         f"topk={_TOP_K}|kw={_KEYWORDS_PER_CLUSTER}|"
         f"v={_PROMPT_VERSION}").encode("utf-8"),
    ).hexdigest()[:16]
    blob_key = _blob_key(slug, mh)
    minio = get_storage()

    if await minio.exists(blob_key):
        try:
            blob = await minio.read_bytes(blob_key)
            cached_keys, refined, original, decisions = load_refine(blob)
            n_changed = int((refined != original).sum())
            n_null = sum(
                1 for d in decisions
                if int(d.get("new_cluster_id", -2)) == -1
            )
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_docs":         int(len(cached_keys)),
                "n_boundary":     len(decisions),
                "n_changed":      n_changed,
                "n_null":         n_null,
                "n_errors":       sum(1 for d in decisions if d.get("error")),
                "wall_ms":        elapsed,
                "store_path":     blob_key,
                "boundary_floor": _BOUNDARY_FLOOR,
                "top_k":          _TOP_K,
                "cache_hit":      True,
            }
            await emit_progress(
                thread_id, "refine", "done",
                n_docs=int(len(cached_keys)), n_boundary=len(decisions),
                n_changed=n_changed, n_null=n_null, wall_ms=elapsed,
                cache_hit=True,
            )
            logger.info(
                f"[refine] {slug}: CACHE HIT — {len(decisions)} boundary docs, "
                f"{n_changed} reassigned, {n_null} null, {elapsed} ms"
            )
            return {"refine_assignments_ref": blob_key,
                    "refine_stats": stats}
        except Exception as e:
            logger.warning(
                f"[refine] {slug}: cached blob {blob_key!r} unreadable "
                f"({type(e).__name__}: {e}); recomputing"
            )

    await emit_progress(thread_id, "refine", "start")

    # ── Load cluster artifacts ─────────────────────────────────────────
    cluster_blob = await minio.read_bytes(cluster_ref)
    cluster_keys, assignments, max_probs, soft = load_clusters(cluster_blob)
    n_docs = len(cluster_keys)
    K = soft.shape[1] if soft.ndim == 2 else 0

    # Identify boundary docs.
    boundary_mask = max_probs < _BOUNDARY_FLOOR
    boundary_indices = np.where(boundary_mask)[0]
    n_boundary = int(boundary_indices.size)

    if n_boundary == 0 or K == 0:
        # Nothing to refine — persist as-is so downstream gets consistent state.
        blob = _pack_npz(cluster_keys, assignments, assignments.copy(), [])
        await minio.write(
            blob_key, blob, content_type="application/octet-stream",
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        stats = {
            "n_docs": n_docs, "n_boundary": 0, "n_changed": 0,
            "n_null": 0, "n_errors": 0,
            "wall_ms": elapsed, "store_path": blob_key,
            "boundary_floor": _BOUNDARY_FLOOR, "top_k": _TOP_K,
            "cache_hit": False, "skipped": "no_boundary_docs",
        }
        await emit_progress(
            thread_id, "refine", "done",
            n_docs=n_docs, n_boundary=0, n_changed=0, n_null=0,
            wall_ms=elapsed,
        )
        return {"refine_assignments_ref": blob_key, "refine_stats": stats}

    # ── Load doc bodies (needed for c-TF-IDF + rep snippets + judge prompts) ─
    bodies = await minio.read_many(cluster_keys)
    await emit_progress(
        thread_id, "refine", "context_prepared",
        n_docs=n_docs, n_boundary=n_boundary, n_clusters=K,
    )

    # ── Per-cluster context: c-TF-IDF keywords + rep snippets ──────────
    cluster_docs_text: dict[int, str] = {}
    for cid in range(K):
        cluster_mask_c = assignments == cid
        if not cluster_mask_c.any():
            continue
        cluster_indices = np.where(cluster_mask_c)[0]
        cluster_docs_text[cid] = " ".join(
            (bodies[i] or "")[:_CTFIDF_DOC_CHARS]
            for i in cluster_indices
        )
    cluster_keywords = _compute_cluster_keywords(cluster_docs_text)
    cluster_snippets: dict[int, str] = {
        cid: _pick_representative_doc(cid, assignments, soft, bodies)
        for cid in cluster_docs_text.keys()
    }

    # ── Refine loop ────────────────────────────────────────────────────
    sem = asyncio.Semaphore(_REFINE_CONCURRENCY)
    judged_done = {"n": 0, "changed": 0, "null": 0, "err": 0}
    _EMIT_EVERY = max(1, n_boundary // 40)

    async def _track(i: int, body: str, candidates: list[int]) -> dict:
        result = await _refine_one(
            sem, i, body, candidates,
            cluster_keywords, cluster_snippets,
            int(assignments[i]),
        )
        judged_done["n"] += 1
        if result.get("error"):
            judged_done["err"] += 1
        if int(result.get("new_cluster_id", -2)) == -1:
            judged_done["null"] += 1
        if int(result.get("new_cluster_id", -2)) != int(assignments[i]):
            judged_done["changed"] += 1
        if (
            judged_done["n"] % _EMIT_EVERY == 0
            or judged_done["n"] == n_boundary
        ):
            await emit_progress(
                thread_id, "refine", "llm_progress",
                judged=judged_done["n"], total=n_boundary,
                changed=judged_done["changed"],
                null=judged_done["null"],
                err=judged_done["err"],
            )
        return result

    tasks = []
    for i in boundary_indices.tolist():
        # Top-K candidate cluster_ids for this doc, sorted by soft membership.
        # Exclude clusters with no docs (cluster_keywords doesn't have them).
        sorted_cids = np.argsort(-soft[int(i)])
        candidates = [
            int(cid) for cid in sorted_cids
            if int(cid) in cluster_keywords
        ][:_TOP_K]
        tasks.append(_track(int(i), bodies[int(i)], candidates))
    decisions = await asyncio.gather(*tasks)

    # ── Build refined assignments ──────────────────────────────────────
    refined = assignments.copy()
    for d in decisions:
        idx = int(d["doc_idx"])
        new_cid = int(d.get("new_cluster_id", refined[idx]))
        refined[idx] = new_cid

    n_changed = int((refined != assignments).sum())
    n_null = sum(
        1 for d in decisions if int(d.get("new_cluster_id", -2)) == -1
    )
    n_errors = sum(1 for d in decisions if d.get("error"))

    # ── Persist to MinIO ───────────────────────────────────────────────
    # Strip heavy meta keys; keep what UI / debug needs.
    decisions_for_blob = [
        {
            "doc_idx":       d["doc_idx"],
            "new_cluster_id": d["new_cluster_id"],
            "confidence":    d.get("confidence"),
            "rationale":     d.get("rationale"),
            "deployment":    (d.get("meta") or {}).get("deployment"),
            "latency_s":     (d.get("meta") or {}).get("latency_s"),
            "error":         d.get("error"),
        }
        for d in decisions
    ]
    blob = _pack_npz(
        cluster_keys, refined, assignments, decisions_for_blob,
    )
    await minio.write(blob_key, blob, content_type="application/octet-stream")

    # ── Bandit deployment usage tally (which models actually answered) ─
    dep_usage: dict[str, int] = {}
    for d in decisions:
        dep = (d.get("meta") or {}).get("deployment") or "?"
        dep_usage[dep] = dep_usage.get(dep, 0) + 1
    deployment_summary = [
        {"deployment": dep, "calls": n}
        for dep, n in sorted(dep_usage.items(), key=lambda kv: -kv[1])
    ]

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_docs":           n_docs,
        "n_boundary":       n_boundary,
        "n_changed":        n_changed,
        "n_null":           n_null,
        "n_errors":         n_errors,
        "wall_ms":          elapsed,
        "store_path":       blob_key,
        "boundary_floor":   _BOUNDARY_FLOOR,
        "top_k":            _TOP_K,
        "cache_hit":        False,
        "blob_bytes":       len(blob),
        "deployment_usage": deployment_summary,
        "prompt_version":   _PROMPT_VERSION,
    }

    await emit_progress(
        thread_id, "refine", "done",
        n_docs=n_docs, n_boundary=n_boundary,
        n_changed=n_changed, n_null=n_null, wall_ms=elapsed,
    )
    logger.info(
        f"[refine] {slug}: {n_boundary} boundary docs judged, "
        f"{n_changed} reassigned, {n_null} sent to noise, "
        f"{n_errors} errors; {elapsed} ms"
    )
    return {"refine_assignments_ref": blob_key, "refine_stats": stats}
