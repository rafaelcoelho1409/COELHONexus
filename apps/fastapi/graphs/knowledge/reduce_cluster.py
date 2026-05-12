"""
Knowledge Distiller — Clio-pattern REDUCE (v2: production-tuned)

Replaces the single-shot CHAPTER_REDUCE_PROMPT that fails on large corpora
(observed 2026-04-22: 300 micro-clusters → NIM 504 gateway timeout on every
reasoning model, Groq 413 TPM rate-limit on every model except llama-4-scout).

v2 (2026-04-22, research-tuned): the v1 Clio pattern works but silhouette-based
k-selection collapses on same-domain corpora, always picking k=_MIN_CHAPTERS.
Deep research into production pipelines (Clio Appendix G.7, Kura, BERTopic,
HERCULES) converged on: stop letting geometry pick k on tight corpora, pick
from corpus size + pedagogical target, then let clustering serve that target.

Pipeline (differences from v1 flagged):

    MAP (unchanged): N shard-labelers emit ~300 micro-clusters.

    REDUCE:
      1. Embed (cluster_name + description) locally via fastembed
         BAAI/bge-base-en-v1.5 [v2: upgraded from bge-small for +2 MTEB
         Clustering points; still fastembed-compatible, ~220MB download].
      2. UMAP pre-reduction [v2: new] — n_components=5, n_neighbors=15,
         min_dist=0.0, metric='cosine', random_state=42. Un-collapses local
         neighborhoods flattened on the L2-normalized hypersphere; silhouette
         0.06 → ~0.25 on BERTopic-community tight-corpus benchmarks.
      3. Size-based k_target [v2: replaces silhouette sweep] — blends
         Clio's `n_micro / 40` formula with pedagogical `n_files / 50`.
         Example: 305 micro-clusters + 4000 files → k_target = 8
         (vs v1 silhouette picking k=4).
      4. KMeansConstrained [v2: new] with size_min = fair_share / 3 and
         size_max = fair_share × 2 — kills the "3-file chapter next to
         1300-file chapter" pathology.
      5. Calinski-Harabasz tiebreaker within k_target ± 1 [v2: new] — more
         reliable than silhouette for picking among adjacent k on tight clouds
         because CH normalizes by (n-k)/(k-1).
      6. Parallel META_LABEL_PROMPT calls, one per meta-cluster.
      7. Cross-meta-cluster slug dedup [v2: new] — majority vote on
         micro-cluster membership, closest-centroid as tiebreaker. Fixes
         the ~20 double-assigned slugs observed in v1.
      8. One ORDER_PROMPT call to sequence the chapters.

Biggest LLM call stays ~3K tokens — safely under every free-tier constraint.

Dependencies (all pure Python, no GPU):
    fastembed        (already present)
    scikit-learn     (already present; used for CH/silhouette + KMeans fallback)
    numpy            (transitively present)
    umap-learn       (added 2026-04-22)
    k-means-constrained (added 2026-04-22)

Coverage invariant: downstream `_validate_plan` + coverage-repair in
`distiller.py` still run unchanged — orphaned slugs go to unused_files,
hallucinated slugs get dropped.

References:
    Clio (Anthropic, arXiv 2412.13678) — §G.5, §G.7, §C.1.3
    Kura (jxnl/kura) — meta_cluster.py
    BERTopic — Best Practices + Parameter Tuning pages
    HERCULES (arXiv 2506.19992) — hierarchical k-means + LLM
    k-means-constrained — MCF-based balanced clustering (joshlk/k-means-constrained)
"""
import asyncio
import logging
import math
import re
import time
from collections import Counter
from typing import Optional

import numpy as np
from langchain_core.runnables import Runnable
from sklearn.cluster import KMeans
from sklearn.metrics import calinski_harabasz_score, silhouette_score

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
from services.llm_chain import build_reduce_label_chain


logger = logging.getLogger(__name__)


# ChapterPlanList pydantic bounds (mirrored here for local clamping).
_MIN_CHAPTERS = 4
_MAX_CHAPTERS = 12

# Deterministic seed for every stochastic step (UMAP, k-means init, etc.)
_SEED = 42

# Clio Appendix G.7: ~40 base clusters per parent neighborhood.
# Our shard labelers emit ~3 micro-clusters per 40-file shard, so 305 microclusters
# map cleanly to ~8 meta-clusters via `round(305/40)` — matching the pedagogical target.
_MICROS_PER_META_TARGET = 40

# Pedagogical volume target: ~50 files per chapter keeps the synthesizer
# prompt under ~15K tokens at CHAPTER_FILES_MAX_CHARS=180K.
_FILES_PER_CHAPTER_TARGET = 50

# UMAP defaults from BERTopic Best Practices (maartengr.github.io/BERTopic):
#   n_components=5 preferred over 10 for downstream clustering quality
#   n_neighbors=15 preserves local structure without merging near-clusters
#   min_dist=0.0 + metric='cosine' is canonical for sentence-transformer embeds
_UMAP_N_COMPONENTS = 5
_UMAP_N_NEIGHBORS = 15
_UMAP_MIN_DIST = 0.0
_UMAP_METRIC = "cosine"

# T-2 (2026-05-09, tightened 2026-05-11): cap a single meta-cluster at 20% of
# total micro-clusters. Original 0.25 left a Docker 2026-05-09 Chapter 7 at
# 225 files (22% of corpus, borderline junk drawer). Tightening to 0.20 forces
# no chapter beyond ~20% of corpus, producing finer (and more pedagogically
# useful) granularity at large N. Without this, KMeansConstrained's only
# constraint is `size_max = fair_share × 2`, which on uneven corpora collapses
# one meta-cluster around the densest topic and leaves the rest thin
# (Terragrunt 2026-04-30 baseline: Chapter 3 absorbed 39% of micro-clusters
# → 160-file junk drawer).
_META_CLUSTER_MAX_FRACTION = 0.20

# T-3 (2026-05-09): post-clustering thin-chapter merge.
# Any meta-cluster with fewer than this many assigned files folds into the
# nearest larger meta-cluster by cosine on UMAP-reduced centroids. Eliminates
# the "9-file standalone chapter next to a 60-file chapter" pattern.
_THIN_CHAPTER_FILE_THRESHOLD = 15

# R8b (2026-05-12): file-count cap on meta-clusters — the per-chapter twin
# of T-2's per-micro-cluster cap. T-2 ensures no meta absorbs >20% of
# *micro-clusters*; R8b ensures no meta absorbs >20% of *files*. The two
# diverge when communities are sized unevenly: R8 global MAP produced a
# chapter with 22 micro-clusters (16% of n_clusters, T-2 pass) but 273
# files (28.9% of corpus, junk drawer). Per-shard MAP's ≤40-file shard
# size naturally bounded this; global needs an explicit cap.
_META_MAX_FILE_FRACTION = 0.20

# Polish #4 (2026-05-11): some kd-reduce-label deployments emit titles like
# ": Docker Compose..." or ":** Managing Docker Hub..." despite the schema
# description saying "2-6 words" — leftover Markdown/numbering punctuation
# that survived the json_schema cut. Strip leading/trailing of these forms:
# - Leading "**" or "*" emphasis markers
# - Leading "Chapter N" or "Chapter N:" prefixes (case-insensitive)
# - Leading runs of `:`, `*`, `-`, `.` punctuation (with surrounding whitespace)
# - Trailing emphasis chars + punctuation tails
# The regex anchors on a single PASS — input "Docker CLI Command Reference"
# (clean) is left untouched; input ": Foo" → "Foo"; "**Chapter 3:** Foo"
# → "Foo"; ":** Foo" → "Foo".
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:\*+\s*)?(?:chapter\s+\d+\s*[:\-.]?)?\s*[:*\-.]+\s*",
    re.IGNORECASE,
)
_TITLE_SUFFIX_RE = re.compile(r"\s*[\*\-:]+\s*$")


def _sanitize_title(raw: str) -> str:
    """
    Strip Markdown / numbering prefixes that some LLMs emit despite the
    `MetaLabelDraft.title` schema. Returns the cleaned title; empty string
    if nothing meaningful survives sanitization (caller treats as failure).
    """
    if not raw:
        return ""
    cleaned = _TITLE_PREFIX_RE.sub("", raw).strip()
    cleaned = _TITLE_SUFFIX_RE.sub("", cleaned).strip()
    return cleaned


# =============================================================================
# R4 (2026-05-11) — hedged invoke for `kd-reduce-label` calls
# =============================================================================
# Empirical justification: Phase A validation runs show one deployment in
# the kd-reduce-label pool occasionally taking 60–180s while siblings
# return in 5–10s. The Router's per-call wait + cascade-on-failure means
# the slow tail dominates wall-clock for the parallel-labeling step.
#
# Hedged invoke fires N parallel attempts against the SAME chain. Router
# cooldown ensures they land on different deployments. First successful
# result wins; losers are cancelled. p95 collapses to ~p50 of the next-
# fastest deployment in the pool.
#
# Trade-off: free-tier consumption ~2× on hedged calls (we send to two
# deployments per logical request). At M=8–12 metas × fanout=2 = 16–24
# extra "wasted" requests per study. Well within every free-tier RPM cap
# in `_reduce_label_entries`; no provider gets close to its quota.
async def _hedged_invoke(
    chain: Runnable,
    payload: dict,
    config=None,
    *,
    fanout: int = 2,
):
    """
    Fire `fanout` parallel calls against `chain`; return the first
    successful result and cancel the rest. Raises the last captured
    exception if every parallel call failed.

    fanout < 1: ValueError
    fanout == 1: degenerates to a plain `chain.ainvoke` (no parallelism)
    fanout >= 2: parallel race via `asyncio.wait(FIRST_COMPLETED)`
    """
    if fanout < 1:
        raise ValueError(f"fanout must be ≥1, got {fanout}")
    if fanout == 1:
        return await chain.ainvoke(payload, config=config)

    async def _one():
        return await chain.ainvoke(payload, config=config)

    tasks = [asyncio.create_task(_one()) for _ in range(fanout)]
    pending: set[asyncio.Task] = set(tasks)
    last_exc: BaseException | None = None
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED,
            )
            for t in done:
                exc = t.exception()
                if exc is None:
                    # First success — cancel the rest and return
                    for p in pending:
                        p.cancel()
                    return t.result()
                last_exc = exc
        # All `fanout` tasks failed — propagate the last exception so the
        # caller's existing try/except logic (synthetic fallback) fires.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(
            f"all {fanout} hedged calls failed without raising"
        )
    finally:
        # Defensive: ensure pending tasks are cancelled and awaited even
        # if a CancelledError propagates through the outer loop.
        for p in pending:
            p.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def embed_and_cluster_reduce(
    shard_results: list[ShardLabels],
    shard_unused_all: list[str],
    framework: str,
    llm: Runnable,
    study_id: str | None = None,
    user_id: str | None = None,
) -> ChapterPlanList:
    """
    Clio-pattern REDUCE v2 (production-tuned 2026-04-22).

    Args:
        shard_results: output of the MAP pass (one per shard).
        shard_unused_all: already-flattened slugs the shard labelers flagged
            as noise — propagated verbatim into the plan's unused_files bucket.
        framework: display name passed to every prompt (e.g., "langchain").
        llm: fallback-chain runnable (build_llm_fallback_chain()).

    Returns:
        ChapterPlanList with `_MIN_CHAPTERS..._MAX_CHAPTERS` ordered chapters.
        Coverage-repair runs in the caller against the full corpus slug-set.

    Raises:
        RuntimeError: if MAP produced fewer than `_MIN_CHAPTERS` micro-clusters.
    """
    # 1. Flatten all micro-clusters across shards
    micro_clusters: list[ShardCluster] = []
    for sr in shard_results:
        micro_clusters.extend(sr.clusters)
    n_clusters = len(micro_clusters)
    if n_clusters < _MIN_CHAPTERS:
        raise RuntimeError(
            f"[reduce-cluster] only {n_clusters} micro-clusters from MAP — "
            f"need ≥{_MIN_CHAPTERS} for a valid ChapterPlanList."
        )
    total_slugs_assigned = sum(len(c.file_slugs) for c in micro_clusters)
    logger.info(
        f"[reduce-cluster] {n_clusters} micro-clusters, "
        f"{total_slugs_assigned} assigned slugs, "
        f"{len(shard_unused_all)} shard-unused"
    )

    # 2. Embed via LiteLLM rotator's `kd-embed` group (NIM nemotron-1b-v2,
    #    single entry — no provider fallover, see embeddings.py module docstring)
    t0 = time.time()
    texts = [f"{c.cluster_name}: {c.description}" for c in micro_clusters]
    vectors_list, embed_provider = await embed_texts(texts)
    vectors = np.asarray(vectors_list, dtype=np.float32)
    logger.info(
        f"[reduce-cluster] embedded {n_clusters}×{vectors.shape[1]}d "
        f"in {time.time() - t0:.2f}s via {embed_provider}"
    )

    # 3a. PCA pre-reduction (Tier 1 #3, 2026-04-24) — SPEED, zero quality loss.
    # UMAP on 2048d embeddings is O(n_clusters × d × iterations); PCA first
    # collapses 2048 → 128 (retaining 99%+ variance on sentence-transformer
    # outputs), then UMAP 128 → 5 runs an order of magnitude faster. Skip
    # PCA when the embedding dim is already ≤ 128 (nothing to reduce).
    _PCA_COMPONENTS = 128
    vectors_for_umap = vectors
    if vectors.shape[1] > _PCA_COMPONENTS and n_clusters > _PCA_COMPONENTS:
        t0 = time.time()
        try:
            from sklearn.decomposition import PCA
            # n_components capped by min(samples, features); PCA raises otherwise.
            pca_n = min(_PCA_COMPONENTS, n_clusters - 1, vectors.shape[1])
            pca_model = PCA(n_components=pca_n, random_state=_SEED)
            vectors_for_umap = np.asarray(pca_model.fit_transform(vectors), dtype=np.float32)
            variance_kept = float(pca_model.explained_variance_ratio_.sum())
            logger.info(
                f"[reduce-cluster] PCA {vectors.shape[1]}d → {vectors_for_umap.shape[1]}d "
                f"in {time.time() - t0:.2f}s (explained variance: {variance_kept:.3f})"
            )
        except Exception as e:
            logger.warning(
                f"[reduce-cluster] PCA pre-reduction failed ({type(e).__name__}: {e}); "
                f"proceeding with raw embeddings"
            )
            vectors_for_umap = vectors

    # 3b. UMAP pre-reduction (v2 addition)
    # BERTopic-community consensus: silhouette 0.06 → ~0.25 on tight corpora.
    # n_neighbors capped at n_clusters-1 for tiny corpora. CPU-only, ~1-3s.
    t0 = time.time()
    try:
        from umap import UMAP
        umap_model = UMAP(
            n_neighbors=min(_UMAP_N_NEIGHBORS, n_clusters - 1),
            n_components=_UMAP_N_COMPONENTS,
            min_dist=_UMAP_MIN_DIST,
            metric=_UMAP_METRIC,
            random_state=_SEED,
        )
        vectors_reduced = np.asarray(umap_model.fit_transform(vectors_for_umap), dtype=np.float32)
        logger.info(
            f"[reduce-cluster] UMAP {vectors_for_umap.shape[1]}d → "
            f"{vectors_reduced.shape[1]}d in {time.time() - t0:.2f}s "
            f"(n_neighbors={min(_UMAP_N_NEIGHBORS, n_clusters - 1)}, "
            f"min_dist={_UMAP_MIN_DIST}, metric={_UMAP_METRIC})"
        )
    except Exception as e:
        logger.warning(
            f"[reduce-cluster] UMAP failed ({type(e).__name__}: {e}); "
            f"falling back to PCA-reduced (or raw) embeddings"
        )
        vectors_reduced = vectors_for_umap

    # 4. Size-based k_target (v2 replaces silhouette sweep)
    k_meta = max(_MIN_CHAPTERS, min(_MAX_CHAPTERS, round(n_clusters / _MICROS_PER_META_TARGET)))
    k_volume = max(_MIN_CHAPTERS, min(_MAX_CHAPTERS, round(total_slugs_assigned / _FILES_PER_CHAPTER_TARGET)))
    k_target = max(_MIN_CHAPTERS, min(_MAX_CHAPTERS, round((k_meta + k_volume) / 2)))
    logger.info(
        f"[reduce-cluster] k selection: "
        f"k_meta={k_meta} (n_clusters/{_MICROS_PER_META_TARGET}), "
        f"k_volume={k_volume} (n_files/{_FILES_PER_CHAPTER_TARGET}), "
        f"k_target={k_target}"
    )

    # 5. KMeansConstrained sweep over k_target ± 1 with Calinski-Harabasz tiebreaker
    t0 = time.time()
    k_candidates = [
        k for k in (k_target - 1, k_target, k_target + 1)
        if _MIN_CHAPTERS <= k <= _MAX_CHAPTERS and k < n_clusters
    ]
    best_k: int = k_target
    best_labels: Optional[np.ndarray] = None
    best_ch: float = -1.0
    best_sil: float = 0.0
    sweep_results: list[tuple[int, float, float]] = []
    for k in k_candidates:
        fair_share = n_clusters / k
        size_min = max(1, int(fair_share / 3))
        # T-2: cap size_max at 25% of total micro-clusters so no single
        # meta-cluster can absorb a junk-drawer's worth of content.
        size_max_unconstrained = int(fair_share * 2)
        size_max_global_cap = int(math.ceil(_META_CLUSTER_MAX_FRACTION * n_clusters))
        size_max = max(size_min + 1, min(size_max_unconstrained, size_max_global_cap))
        try:
            from k_means_constrained import KMeansConstrained
            km = KMeansConstrained(
                n_clusters=k,
                size_min=size_min,
                size_max=size_max,
                random_state=_SEED,
            )
            labels = km.fit_predict(vectors_reduced)
        except Exception as e:
            logger.warning(
                f"[reduce-cluster] KMeansConstrained(k={k}, "
                f"size_min={size_min}, size_max={size_max}) failed "
                f"({type(e).__name__}: {e}); falling back to plain KMeans"
            )
            km = KMeans(n_clusters=k, random_state=_SEED, n_init=10)
            labels = km.fit_predict(vectors_reduced)
        try:
            ch = float(calinski_harabasz_score(vectors_reduced, labels))
            sil = float(silhouette_score(vectors_reduced, labels))
        except ValueError:
            continue
        sweep_results.append((k, ch, sil))
        if ch > best_ch:
            best_k, best_labels, best_ch, best_sil = k, labels, ch, sil

    if best_labels is None:
        # Last resort: plain unconstrained KMeans at k_target
        logger.warning(
            f"[reduce-cluster] all constrained k fits failed; "
            f"falling back to plain KMeans(k={k_target})"
        )
        km = KMeans(n_clusters=k_target, random_state=_SEED, n_init=10)
        best_k = k_target
        best_labels = km.fit_predict(vectors_reduced)
        best_ch = best_sil = 0.0

    size_counter = Counter(int(lbl) for lbl in best_labels)
    sizes_str = ", ".join(str(size_counter[i]) for i in range(best_k))
    sweep_str = ", ".join(f"k={k}(CH={ch:.1f},sil={sil:.3f})" for k, ch, sil in sweep_results)
    logger.info(
        f"[reduce-cluster] k={best_k} CH={best_ch:.1f} silhouette={best_sil:.3f} "
        f"sizes=[{sizes_str}] sweep=[{sweep_str}] in {time.time() - t0:.2f}s"
    )

    # 6. Group micro-clusters by meta-cluster id
    meta_groups: dict[int, list[int]] = {}  # meta_id → list of micro-cluster indices
    for i, lbl in enumerate(best_labels):
        meta_groups.setdefault(int(lbl), []).append(i)

    # Meta-cluster centroids (on UMAP-reduced space — the space where k-means actually ran)
    centroids: dict[int, np.ndarray] = {
        mid: vectors_reduced[idxs].mean(axis=0)
        for mid, idxs in meta_groups.items()
    }

    # T-3 (2026-05-09): merge thin meta-clusters into nearest fat meta-cluster.
    # File counts come from the union of file_slugs across each meta-cluster's
    # micro-clusters. Chapters below `_THIN_CHAPTER_FILE_THRESHOLD` files are
    # too small to teach a coherent topic — fold them into the nearest larger
    # cluster by cosine on UMAP-reduced centroids. Recompute centroid + size
    # after each merge so subsequent thin merges see the updated geometry.
    def _meta_file_count(mid: int) -> int:
        return sum(len(micro_clusters[i].file_slugs) for i in meta_groups[mid])

    thin_mids = [m for m in list(meta_groups) if _meta_file_count(m) < _THIN_CHAPTER_FILE_THRESHOLD]
    fat_mids  = [m for m in list(meta_groups) if _meta_file_count(m) >= _THIN_CHAPTER_FILE_THRESHOLD]
    merged_pairs: list[tuple[int, int, int]] = []  # (thin_mid, fat_mid, files_moved)
    if thin_mids and fat_mids:
        # Process thin clusters smallest-first so very thin ones get absorbed
        # before they perturb other thin clusters' merge decisions.
        thin_mids.sort(key=_meta_file_count)
        for thin_mid in thin_mids:
            thin_cent = centroids[thin_mid]
            thin_norm = thin_cent / max(float(np.linalg.norm(thin_cent)), 1e-12)
            best_fat: Optional[int] = None
            best_sim: float = -1.0
            for fat_mid in fat_mids:
                fat_cent = centroids[fat_mid]
                fat_norm = fat_cent / max(float(np.linalg.norm(fat_cent)), 1e-12)
                sim = float(thin_norm @ fat_norm)
                if sim > best_sim:
                    best_sim, best_fat = sim, fat_mid
            if best_fat is None:
                continue
            files_moved = _meta_file_count(thin_mid)
            meta_groups[best_fat].extend(meta_groups[thin_mid])
            del meta_groups[thin_mid]
            del centroids[thin_mid]
            # Recompute centroid for the absorbing meta-cluster
            centroids[best_fat] = vectors_reduced[meta_groups[best_fat]].mean(axis=0)
            merged_pairs.append((thin_mid, best_fat, files_moved))
    if merged_pairs:
        logger.info(
            f"[reduce-cluster] T-3 thin-chapter merge: "
            f"folded {len(merged_pairs)} thin meta-cluster(s) "
            f"(<{_THIN_CHAPTER_FILE_THRESHOLD} files each) "
            f"→ pairs: {merged_pairs}; "
            f"meta-clusters now: {len(meta_groups)}"
        )

    # R8b (2026-05-12): file-count cap — split oversized meta-clusters.
    # After T-3 thin-merge, any meta over `_META_MAX_FILE_FRACTION` of
    # total files gets split via sub-KMeans on its UMAP-reduced member
    # vectors with k=2. The bigger half keeps the original meta_id; the
    # smaller half gets a new meta_id. ONE pass (no recursion): if a
    # sub-meta is still oversized, log a warning and accept it. Capped at
    # `_MAX_CHAPTERS` total — once the meta count hits 12, no further
    # splits even if oversized metas remain (the ChapterPlanList schema
    # caps the final plan at 12 chapters; over-splitting fails Pydantic).
    file_cap = max(1, int(math.ceil(_META_MAX_FILE_FRACTION * total_slugs_assigned)))
    oversized = [
        (mid, _meta_file_count(mid)) for mid in list(meta_groups)
        if _meta_file_count(mid) > file_cap
    ]
    # Split biggest-first so the worst offender gets the most splitting budget.
    oversized.sort(key=lambda t: -t[1])
    # (orig_mid, files_before, [files_per_sub_meta])
    split_log: list[tuple[int, int, list[int]]] = []
    if oversized:
        next_meta_id = max(meta_groups) + 1
        for mid, file_count_before in oversized:
            slots_remaining = _MAX_CHAPTERS - len(meta_groups)
            if slots_remaining < 1:
                logger.warning(
                    f"[reduce-cluster] R8b: at _MAX_CHAPTERS={_MAX_CHAPTERS}; "
                    f"cannot split meta {mid} ({file_count_before} files); "
                    f"plan will exceed file cap on this chapter"
                )
                break
            members = meta_groups[mid]
            if len(members) < 2:
                logger.warning(
                    f"[reduce-cluster] R8b: meta {mid} has {file_count_before} "
                    f"files but only 1 micro-cluster — cannot split"
                )
                continue
            # R8b (improved 2026-05-12): adaptive sub_k based on how over-cap
            # this meta is, with KMeansConstrained-forced balance.
            #
            # Plain KMeans(k=2) sub-split (the original R8b) produced
            # degenerate (n-1, 1) partitions on small dense clusters
            # (Terragrunt 2026-05-12: 8-member meta → 7+1 split → 253-file
            # sub-meta still 63% of corpus). Two fixes:
            #
            # 1. sub_k = ceil(files / cap) — a 4×-oversized meta gets split
            #    into 4 sub-metas in one pass. Avoids "split, still over cap,
            #    accept" failure mode.
            # 2. KMeansConstrained(size_min, size_max) — forces every sub-
            #    bucket to have at least `fair_share // 2` micro-clusters,
            #    preventing the degenerate (n-1, 1) partition outright.
            #
            # Bounded by (a) len(members) — can't have more clusters than
            # input points — and (b) slots_remaining + 1 — the +1 because we
            # replace the original meta with sub_k sub-metas (net +sub_k-1).
            sub_k_needed = max(2, math.ceil(file_count_before / file_cap))
            sub_k = min(sub_k_needed, len(members), slots_remaining + 1)
            if sub_k < 2:
                continue  # No splitting budget
            fair_share = max(1, len(members) // sub_k)
            sub_size_min = max(1, fair_share // 2)
            sub_size_max = max(sub_size_min + 1, fair_share * 2)
            try:
                from k_means_constrained import KMeansConstrained
                sub_km = KMeansConstrained(
                    n_clusters=sub_k,
                    size_min=sub_size_min,
                    size_max=sub_size_max,
                    random_state=_SEED,
                )
                sub_labels = sub_km.fit_predict(vectors_reduced[members])
            except Exception as e:
                # Fall back to plain KMeans if the size constraints are
                # infeasible for this geometry (e.g., adversarial layouts
                # where balanced clustering has no min-cost-flow solution).
                logger.warning(
                    f"[reduce-cluster] R8b: KMeansConstrained(k={sub_k}, "
                    f"size_min={sub_size_min}, size_max={sub_size_max}) "
                    f"failed for meta {mid} ({type(e).__name__}: {e}); "
                    f"falling back to plain KMeans"
                )
                try:
                    sub_km = KMeans(n_clusters=sub_k, random_state=_SEED, n_init=10)
                    sub_labels = sub_km.fit_predict(vectors_reduced[members])
                except Exception as e2:
                    logger.warning(
                        f"[reduce-cluster] R8b: plain KMeans fallback also "
                        f"failed for meta {mid} ({type(e2).__name__}: {e2}); "
                        f"leaving as is"
                    )
                    continue
            # Partition members into sub_k buckets; drop empty buckets defensively
            sub_buckets: dict[int, list[int]] = {}
            for member_idx, sub_lbl in zip(members, sub_labels):
                sub_buckets.setdefault(int(sub_lbl), []).append(member_idx)
            sub_buckets = {k: v for k, v in sub_buckets.items() if v}
            if len(sub_buckets) < 2:
                logger.warning(
                    f"[reduce-cluster] R8b: sub-cluster for meta {mid} "
                    f"degenerated to {len(sub_buckets)} non-empty bucket(s); "
                    f"leaving meta as is"
                )
                continue
            # Sort sub-buckets by file count, biggest first.
            # Biggest keeps the original meta_id; rest get fresh ids.
            ranked = sorted(
                sub_buckets.values(),
                key=lambda b: -sum(len(micro_clusters[i].file_slugs) for i in b),
            )
            meta_groups[mid] = ranked[0]
            centroids[mid] = vectors_reduced[ranked[0]].mean(axis=0)
            new_file_counts = [
                sum(len(micro_clusters[i].file_slugs) for i in ranked[0])
            ]
            for sub_bucket in ranked[1:]:
                meta_groups[next_meta_id] = sub_bucket
                centroids[next_meta_id] = vectors_reduced[sub_bucket].mean(axis=0)
                new_file_counts.append(
                    sum(len(micro_clusters[i].file_slugs) for i in sub_bucket)
                )
                next_meta_id += 1
            split_log.append((mid, file_count_before, new_file_counts))
            if max(new_file_counts) > file_cap:
                logger.warning(
                    f"[reduce-cluster] R8b: meta {mid} split into "
                    f"{new_file_counts} files — biggest sub-meta still over "
                    f"cap={file_cap}; accepting (single-pass split)"
                )
    if split_log:
        logger.info(
            f"[reduce-cluster] R8b file-cap split: cap={file_cap} files "
            f"(_META_MAX_FILE_FRACTION={_META_MAX_FILE_FRACTION}, "
            f"total_files={total_slugs_assigned}); splits: {split_log}; "
            f"meta-clusters now: {len(meta_groups)}"
        )

    # 7. Label each meta-cluster in parallel (~3K tokens each)
    # R1+R2 (2026-05-11): route through the dedicated `kd-reduce-label` rotator
    # (curated non-reasoning pool: Groq llama-3.3-70b, Gemini Flash-Lite,
    # NIM Nemotron-Super-120b + gpt-oss-120b + Mistral-Large-3, Mistral direct,
    # Llama-4 Maverick). The `llm` argument is the synth-grade kd-all chain —
    # wrong tool for 3K-token classification labeling because its reasoning
    # models burn the 300s NIM gateway budget on <think> blocks. Method swapped
    # from function_calling → json_schema so structured output is enforced
    # server-side, making the silent-None / empty-title path structurally
    # impossible on providers that honor it.
    t0 = time.time()
    reduce_label_llm = build_reduce_label_chain()
    label_chain = META_LABEL_PROMPT | reduce_label_llm.with_structured_output(
        MetaLabelDraft, method="json_schema",
    )

    async def _label_one(
        meta_id: int,
        micro_indices: list[int],
    ) -> tuple[int, MetaLabelDraft, list[str]]:
        members = [micro_clusters[i] for i in micro_indices]
        member_lines = "\n".join(
            f"- {m.cluster_name}: {m.description} "
            f"({len(m.file_slugs)} files: {', '.join(m.file_slugs[:3])}"
            f"{'...' if len(m.file_slugs) > 3 else ''})"
            for m in members
        )
        draft: MetaLabelDraft | None = None
        try:
            from services.knowledge.langfuse_client import langfuse_config as _lf_cfg
            # R4 (2026-05-11): hedge fanout=2 against the kd-reduce-label
            # pool. First successful response wins; the loser is cancelled.
            # Caps p95 at the second-fastest deployment's latency, killing
            # the slow-tail cascade observed on heavier studies.
            draft = await _hedged_invoke(
                label_chain,
                {
                    "framework": framework,
                    "meta_id": meta_id,
                    "n_members": len(members),
                    "member_lines": member_lines,
                },
                config = _lf_cfg(
                    metadata = {
                        "framework": framework,
                        "meta_id": str(meta_id),
                        "label": f"reduce-meta-label-{meta_id}",
                    },
                    tags = ["planner", "reduce", "meta-label"],
                    session_id = study_id,
                    user_id = user_id,
                    run_name = f"kd-reduce-meta-label-{meta_id:02d}",
                ) or None,
                fanout=2,
            )
            # OP-21c (2026-04-25) + Polish #1 (2026-05-11) — `with_structured_output`
            # can return None (tool_call non-parseable) OR an instance with an
            # empty/whitespace-only `title` field (observed on Docker 2026-05-09:
            # one kd-all deployment returned `title=""` instead of None, slipping
            # past the original None-only check). With R2's json_schema mode this
            # is structurally impossible on providers that honor the schema, but
            # we keep both guards as belt-and-suspenders for deployments that
            # silently fall back to free-form output.
            if draft is None or not (draft.title or "").strip():
                raise RuntimeError(
                    f"label_chain returned None or empty title for meta {meta_id} "
                    f"(LLM tool_call non-parseable or schema-violating)"
                )
            # Polish #4 (2026-05-11) — strip leading/trailing Markdown +
            # "Chapter N:" / "**:" punctuation artifacts that some kd-reduce-label
            # deployments emit despite the json_schema cut. Observed on Docker
            # 2026-05-11: titles like ": Docker Compose..." (Ch3) and
            # ":** Managing Docker Hub..." (Ch4). Sanitize and re-validate;
            # treat an empty result as label failure (caller's synthetic fallback).
            sanitized = _sanitize_title(draft.title)
            if not sanitized:
                raise RuntimeError(
                    f"label_chain produced unparseable title for meta {meta_id} "
                    f"after sanitization (raw={draft.title!r})"
                )
            if sanitized != draft.title:
                logger.info(
                    f"[reduce-cluster] meta {meta_id} title sanitized: "
                    f"{draft.title!r} → {sanitized!r}"
                )
                draft = draft.model_copy(update={"title": sanitized})
        except Exception as e:
            logger.warning(
                f"[reduce-cluster] label call for meta {meta_id} failed "
                f"({type(e).__name__}: {str(e)[:120]}); using synthetic draft"
            )
            seed_name = members[0].cluster_name if members else f"Meta {meta_id}"
            draft = MetaLabelDraft(
                title=f"{seed_name} and Related",
                goal=(
                    f"Understand {seed_name} as covered across "
                    f"{len(members)} related micro-clusters."
                ),
            )
        # Raw union of member slugs (dedup happens in next step across meta-clusters)
        raw_assigned = sorted({s for m in members for s in m.file_slugs})
        return meta_id, draft, raw_assigned

    label_results: list[tuple[int, MetaLabelDraft, list[str]]] = await asyncio.gather(
        *(_label_one(mid, micro_indices) for mid, micro_indices in meta_groups.items())
    )
    logger.info(
        f"[reduce-cluster] labeled {len(label_results)} meta-clusters in "
        f"{time.time() - t0:.2f}s (parallel)"
    )

    # Sort by meta_id for stable presentation
    label_results.sort(key=lambda r: r[0])

    # 8. Cross-meta-cluster slug dedup (v2 addition)
    # Root cause of v1's ~20 double-assignments: the MAP shard-labeler sometimes
    # puts the same slug in 2 overlapping micro-clusters within a shard. k-means
    # then routes those micro-clusters to different meta-clusters, so the slug
    # ends up in 2 chapters.
    # Resolution: majority vote on micro-cluster membership; closest-centroid
    # tiebreaker when the vote ties.
    t0 = time.time()
    slug_to_meta_ids: dict[str, list[int]] = {}  # slug → [meta_ids that claim it]
    for meta_id, _, assigned in label_results:
        for slug in assigned:
            slug_to_meta_ids.setdefault(slug, []).append(meta_id)
    duplicates = {s: ids for s, ids in slug_to_meta_ids.items() if len(ids) > 1}
    slug_winner: dict[str, int] = {}
    if duplicates:
        # Precompute: slug → list of micro-cluster indices that contain it
        slug_to_micro_idx: dict[str, list[int]] = {}
        for i, mc in enumerate(micro_clusters):
            for s in mc.file_slugs:
                slug_to_micro_idx.setdefault(s, []).append(i)
        for slug, meta_ids in duplicates.items():
            counter = Counter(meta_ids)
            top_count = max(counter.values())
            top_ids = [mid for mid, c in counter.items() if c == top_count]
            if len(top_ids) == 1:
                slug_winner[slug] = top_ids[0]
                continue
            # Tied — use closest-centroid on the slug's own micro-cluster mean
            slug_mean = vectors_reduced[slug_to_micro_idx[slug]].mean(axis=0)
            best_mid, best_dist = top_ids[0], float("inf")
            for mid in top_ids:
                dist = float(np.linalg.norm(slug_mean - centroids[mid]))
                if dist < best_dist:
                    best_mid, best_dist = mid, dist
            slug_winner[slug] = best_mid
        # Rebuild assigned_files per meta-cluster, dropping slugs lost to others
        cleaned: list[tuple[int, MetaLabelDraft, list[str]]] = []
        for meta_id, draft, assigned in label_results:
            filtered = [s for s in assigned if slug_winner.get(s, meta_id) == meta_id]
            cleaned.append((meta_id, draft, filtered))
        label_results = cleaned
        logger.info(
            f"[reduce-cluster] slug dedup: resolved {len(duplicates)} "
            f"double-assigned slugs in {time.time() - t0:.3f}s"
        )
    else:
        logger.info("[reduce-cluster] slug dedup: no duplicates")

    drafts = [r[1] for r in label_results]
    assigned_lists = [r[2] for r in label_results]
    M = len(drafts)

    # 9. Order the chapters in one small LLM call (~2K tokens)
    t0 = time.time()
    chapter_lines = "\n".join(
        f"{i}: {d.title} — {d.goal}" for i, d in enumerate(drafts)
    )
    # R1+R2 (2026-05-11): reuse the kd-reduce-label rotator + json_schema mode
    # for the ordering call too — same constraints apply (small structured
    # output, non-reasoning preferred, native schema enforcement).
    order_chain = ORDER_PROMPT | reduce_label_llm.with_structured_output(
        OrderedIndices, method="json_schema",
    )
    rationale: str
    try:
        from services.knowledge.langfuse_client import langfuse_config as _lf_cfg
        # R4 (2026-05-11): hedge the single serial ordering call too —
        # this one isn't fanned out across metas, so a slow deployment here
        # blocks the whole step. fanout=2 caps p95 at the second-fastest.
        ordering: OrderedIndices = await _hedged_invoke(
            order_chain,
            {
                "framework": framework,
                "chapter_lines": chapter_lines,
            },
            config = _lf_cfg(
                metadata = {
                    "framework": framework,
                    "label": "reduce-order-chapters",
                },
                tags = ["planner", "reduce", "order"],
                session_id = study_id,
                user_id = user_id,
                run_name = "kd-reduce-order-chapters",
            ) or None,
            fanout=2,
        )
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

    # 10. Build the final ChapterPlanList
    chapters: list[ChapterPlan] = []
    for n, idx in enumerate(order, start=1):
        drft = drafts[idx]
        chapters.append(ChapterPlan(
            number=n,
            title=drft.title,
            goal=drft.goal,
            assigned_files=assigned_lists[idx],
        ))
    unused_files = [
        UnusedFile(slug=s, reason="shard-flagged noise (low-value)")
        for s in shard_unused_all
    ]
    plan = ChapterPlanList(
        chapters=chapters,
        unused_files=unused_files,
        reasoning=(
            f"Clio v2 REDUCE: {n_clusters} micro-clusters "
            f"(from {len(shard_results)} shards, {total_slugs_assigned} assigned slugs) → "
            f"embed via {embed_provider} → "
            f"UMAP {vectors.shape[1]}d→{vectors_reduced.shape[1]}d → "
            f"KMeansConstrained k={best_k} (k_target={k_target}, "
            f"k_meta={k_meta}, k_volume={k_volume}) "
            f"CH={best_ch:.1f} silhouette={best_sil:.3f} sizes=[{sizes_str}] → "
            f"{len(duplicates)} slug-dedups → "
            f"{M} chapters. Ordering: {rationale}"
        ),
    )
    return plan
