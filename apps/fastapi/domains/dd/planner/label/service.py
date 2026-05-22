from __future__ import annotations

import asyncio
import json

import numpy as np

from domains.llm.rotator.chain import chat_judge_bandit_async

from .constants import (
    _BLOB_PREFIX,
    _JSON_RE,
    _KEYWORDS_TOP_K,
    _LEADING_LABEL_RE,
    _MAX_TOKENS,
    _N_SAMPLES,
    _NOISE_LABEL,
    _REP_DOCS_PER_CLUSTER,
    _REP_DOC_CHARS,
    _TEMPERATURE,
)


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


def load_labels(text: str) -> dict[int, str]:
    """Convenience loader for downstream nodes (reduce, validate)."""
    payload = json.loads(text)
    raw = payload.get("labels") or {}
    return {int(k): str(v) for k, v in raw.items()}
