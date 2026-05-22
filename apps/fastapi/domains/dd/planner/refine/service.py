from __future__ import annotations

import asyncio
import io
import json
import random

import numpy as np

from domains.llm.rotator.chain import chat_judge_bandit_async

from .constants import (
    _BLOB_PREFIX,
    _DOC_BODY_CHARS,
    _JSON_RE,
    _KEYWORDS_PER_CLUSTER,
    _LABELS,
    _REFINE_MAX_TOKENS,
    _SNIPPET_CHARS,
)


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
