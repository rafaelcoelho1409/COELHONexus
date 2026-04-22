"""
Knowledge Distiller — Clio-pattern REDUCE (embed + k-means + label + order)

Replaces the single-shot CHAPTER_REDUCE_PROMPT call that fails on large
corpora (observed 2026-04-22: 300 micro-clusters → NIM 504 gateway timeout
every attempt, Groq 413 TPM rate-limit on llama-3.3-70b-versatile).

The Clio pattern (Anthropic, arxiv 2412.13678 §Hierarchizer):
    MAP pass (unchanged): N shard-labelers emit ~300 micro-clusters.
    Embed (cluster_name + description) locally via fastembed ONNX.
    k-means groups vectors into M meta-clusters; silhouette picks M∈[4,12].
    For each meta-cluster, one small LLM call emits (title, goal).
    One small LLM call orders the M chapters.
    assigned_files = union of member micro-clusters' file_slugs (deterministic).

Why it works where the single-shot REDUCE fails:
    Biggest LLM prompt becomes ~3K tokens (one meta-cluster's member list) —
    fits every Groq free-tier TPM cap AND completes well inside NIM's 300s
    gateway window. No single-point-of-failure call: M labeling calls run
    in parallel via asyncio.gather, each with the full fallback chain.

Coverage invariant:
    Not enforced here — the caller (graphs/knowledge/distiller.py:planner)
    runs the existing deterministic coverage-repair step after this function
    returns. Orphaned slugs go to unused_files; hallucinated slugs get
    dropped. Those passes are schema-agnostic and work unchanged.
"""
import asyncio
import logging
import time
from collections import Counter
from typing import Optional

import numpy as np
from langchain_core.runnables import Runnable
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

from schemas.knowledge.agents import (
    ChapterPlan,
    ChapterPlanList,
    MetaLabelDraft,
    OrderedIndices,
    ShardCluster,
    ShardLabels,
    UnusedFile,
)
from schemas.knowledge.prompts import META_LABEL_PROMPT, ORDER_PROMPT
from services.knowledge.embeddings import embed_texts


logger = logging.getLogger(__name__)


# Pydantic constraints on ChapterPlanList: chapters must be 4..12.
_MIN_CHAPTERS = 4
_MAX_CHAPTERS = 12

# k-means PRNG seed — deterministic clustering per run given same inputs.
_KMEANS_SEED = 42


async def embed_and_cluster_reduce(
    shard_results: list[ShardLabels],
    shard_unused_all: list[str],
    framework: str,
    llm: Runnable,
) -> ChapterPlanList:
    """
    Clio-pattern REDUCE.

    Args:
        shard_results: output of the MAP pass (one per shard; contains the
            shard's micro-clusters and unused_shard_slugs).
        shard_unused_all: already-flattened list of slugs the shard
            labelers flagged as noise. Propagated into the plan's
            unused_files bucket verbatim.
        framework: display name passed to every prompt (e.g., "langchain").
        llm: the fallback-chain runnable (build_llm_fallback_chain()).

    Returns:
        ChapterPlanList with 4..12 ordered chapters. Coverage-repair runs
        in the caller against the full corpus slug-set.

    Raises:
        RuntimeError: if the MAP pass produced fewer than _MIN_CHAPTERS
        micro-clusters — that corpus is too small for a map-reduce shape;
        caller should fall back to the single-shot PLANNER_PROMPT path.
    """
    # 1. Flatten all micro-clusters across shards
    micro_clusters: list[ShardCluster] = []
    for sr in shard_results:
        micro_clusters.extend(sr.clusters)
    n_clusters = len(micro_clusters)
    if n_clusters < _MIN_CHAPTERS:
        raise RuntimeError(
            f"[reduce-cluster] only {n_clusters} micro-clusters from MAP — "
            f"need ≥{_MIN_CHAPTERS} for a valid ChapterPlanList. Corpus is "
            f"too small for map-reduce; caller should use single-shot planner."
        )
    total_slugs = sum(len(c.file_slugs) for c in micro_clusters)
    logger.info(
        f"[reduce-cluster] {n_clusters} micro-clusters, "
        f"{total_slugs} assigned slugs, "
        f"{len(shard_unused_all)} shard-unused"
    )

    # 2. Embed (cluster_name + description) locally — no external API
    t0 = time.time()
    texts = [f"{c.cluster_name}: {c.description}" for c in micro_clusters]
    vectors_list = await embed_texts(texts)
    vectors = np.asarray(vectors_list, dtype = np.float32)
    logger.info(
        f"[reduce-cluster] embedded {n_clusters}×{vectors.shape[1]}d "
        f"in {time.time() - t0:.2f}s (fastembed BAAI/bge-small-en-v1.5)"
    )

    # 3. k-means sweep with silhouette selection
    # k upper bound: ≤12 (schema cap), and ≤ n_clusters//3 so meta-clusters
    # stay meaningful (no meta-cluster with 1-2 members dominating).
    t0 = time.time()
    k_max = min(_MAX_CHAPTERS, max(_MIN_CHAPTERS, n_clusters // 3))
    best_k: int = _MIN_CHAPTERS
    best_labels: Optional[np.ndarray] = None
    best_score: float = -1.0
    for k in range(_MIN_CHAPTERS, k_max + 1):
        if k >= n_clusters:
            break
        km = KMeans(n_clusters = k, random_state = _KMEANS_SEED, n_init = 10)
        labels = km.fit_predict(vectors)
        try:
            score = float(silhouette_score(vectors, labels))
        except ValueError:
            continue
        if score > best_score:
            best_k, best_labels, best_score = k, labels, score

    if best_labels is None:
        # Silhouette couldn't score any k (e.g., all points identical).
        # Fall back to k = _MIN_CHAPTERS so downstream pydantic validation passes.
        km = KMeans(n_clusters = _MIN_CHAPTERS, random_state = _KMEANS_SEED, n_init = 10)
        best_k = _MIN_CHAPTERS
        best_labels = km.fit_predict(vectors)
        best_score = -1.0

    size_counter = Counter(int(lbl) for lbl in best_labels)
    sizes_str = ", ".join(str(size_counter[i]) for i in range(best_k))
    logger.info(
        f"[reduce-cluster] k-means k={best_k} silhouette={best_score:.3f} "
        f"sizes=[{sizes_str}] in {time.time() - t0:.2f}s"
    )

    # 4. Group micro-clusters by meta-cluster id (stable over runs given
    #    the seed; exact cluster-id numbering doesn't matter — ordering
    #    pass handles reading order).
    meta_groups: dict[int, list[ShardCluster]] = {}
    for mc, lbl in zip(micro_clusters, best_labels):
        meta_groups.setdefault(int(lbl), []).append(mc)

    # 5. Label each meta-cluster in parallel.
    # Each prompt is ~3K tokens (30 member lines × ~100 chars) — safely
    # under every fallback-chain model's limit.
    t0 = time.time()
    label_chain = META_LABEL_PROMPT | llm.with_structured_output(
        MetaLabelDraft, method = "function_calling",
    )

    async def _label_one(
        meta_id: int,
        members: list[ShardCluster],
    ) -> tuple[int, MetaLabelDraft, list[str]]:
        member_lines = "\n".join(
            f"- {m.cluster_name}: {m.description} "
            f"({len(m.file_slugs)} files: {', '.join(m.file_slugs[:3])}"
            f"{'...' if len(m.file_slugs) > 3 else ''})"
            for m in members
        )
        try:
            draft: MetaLabelDraft = await label_chain.ainvoke({
                "framework": framework,
                "meta_id": meta_id,
                "n_members": len(members),
                "member_lines": member_lines,
            })
        except Exception as e:
            # One flaky label call should not kill the whole REDUCE.
            # Emit a synthetic best-effort title; the critic may flag it
            # downstream but the pipeline keeps moving.
            logger.warning(
                f"[reduce-cluster] label call for meta {meta_id} failed "
                f"({type(e).__name__}: {str(e)[:120]}); using synthetic draft"
            )
            seed_name = members[0].cluster_name if members else f"Meta {meta_id}"
            draft = MetaLabelDraft(
                title = f"{seed_name} and Related",
                goal = (
                    f"Understand {seed_name} as covered across "
                    f"{len(members)} related micro-clusters."
                ),
            )
        # Deterministic union of member slugs — no LLM trip needed here
        assigned = sorted({s for m in members for s in m.file_slugs})
        return meta_id, draft, assigned

    label_results: list[tuple[int, MetaLabelDraft, list[str]]] = await asyncio.gather(
        *(_label_one(mid, members) for mid, members in meta_groups.items())
    )
    logger.info(
        f"[reduce-cluster] labeled {len(label_results)} meta-clusters in "
        f"{time.time() - t0:.2f}s (parallel)"
    )

    # Sort by meta_id so the ORDER_PROMPT sees a stable index space
    label_results.sort(key = lambda r: r[0])
    drafts = [r[1] for r in label_results]
    assigned_lists = [r[2] for r in label_results]
    M = len(drafts)

    # 6. Order the chapters in one small LLM call (~2K tokens).
    t0 = time.time()
    chapter_lines = "\n".join(
        f"{i}: {d.title} — {d.goal}" for i, d in enumerate(drafts)
    )
    order_chain = ORDER_PROMPT | llm.with_structured_output(
        OrderedIndices, method = "function_calling",
    )
    rationale: str
    try:
        ordering: OrderedIndices = await order_chain.ainvoke({
            "framework": framework,
            "chapter_lines": chapter_lines,
        })
        proposed = [int(i) for i in ordering.order]
        if len(proposed) == M and set(proposed) == set(range(M)):
            order = proposed
            rationale = ordering.rationale
        else:
            logger.warning(
                f"[reduce-cluster] ordering pass returned invalid permutation "
                f"({proposed} for M={M}); using input order"
            )
            order = list(range(M))
            rationale = (
                f"LLM ordering returned invalid permutation (got {proposed} "
                f"for M={M}); reverted to input order."
            )
    except Exception as e:
        logger.warning(
            f"[reduce-cluster] ordering call failed "
            f"({type(e).__name__}: {str(e)[:120]}); using input order"
        )
        order = list(range(M))
        rationale = (
            f"Ordering LLM call failed ({type(e).__name__}); "
            f"reverted to input order."
        )
    logger.info(
        f"[reduce-cluster] ordered {M} chapters in {time.time() - t0:.2f}s"
    )

    # 7. Build the final ChapterPlanList
    chapters: list[ChapterPlan] = []
    for n, idx in enumerate(order, start = 1):
        drft = drafts[idx]
        chapters.append(ChapterPlan(
            number = n,
            title = drft.title,
            goal = drft.goal,
            assigned_files = assigned_lists[idx],
        ))
    unused_files = [
        UnusedFile(slug = s, reason = "shard-flagged noise (low-value)")
        for s in shard_unused_all
    ]
    plan = ChapterPlanList(
        chapters = chapters,
        unused_files = unused_files,
        reasoning = (
            f"Clio-pattern REDUCE: {n_clusters} micro-clusters "
            f"(from {len(shard_results)} shards) → "
            f"k-means k={best_k} silhouette={best_score:.3f} "
            f"sizes=[{sizes_str}] → {M} chapters. "
            f"Ordering: {rationale}"
        ),
    )
    return plan
