"""
Knowledge Distiller — Planner Progress Reporter (2026-05-15).

Sibling of `services/knowledge/ingest_progress.py`. Same shape: throttled
Redis writes per sub-step, consumed by the FastHTML observability page at
`/kd/studies/{id}/observability/planner`. Stage 2 of the per-node viz stack
(see `docs/KD-PIPELINE-SUBSTEP-MAP-2026-05-15.md`).

DESIGN PRINCIPLE

  The viz exists to MAP IMPROVEMENT POINTS — every method surfaces a score
  or decision the planner made that an operator could second-guess:
  cosine sims (off-topic, dedup), strict-JSON vs fallback path (MAP),
  CH + silhouette sweep (REDUCE k-selection), title-vs-files coherence
  (the Ch02 mis-routing detector). The state of "this happened" is
  secondary; the state of "this happened with score X, threshold Y, decision
  Z" is the point.

REDIS LAYOUT

  Each sub-step writes to its own key so the page can render independently
  as data arrives (planner has long-running async substeps; we don't want
  the page blank until everything is done).

  Status:
    planner:status:{study_id}          dict — phase, elapsed_ms, error_msg
  Sub-steps:
    planner:corpus_load:{study_id}     dict — N, byte distribution
    planner:off_topic:{study_id}       dict — drop_count, threshold, per-file cosines
    planner:dedup:{study_id}           dict — drop_count, dedup_log[]
    planner:cache:{study_id}           dict — hit/miss, manifest_hash, age
    planner:shards:{study_id}          dict — total_shards, shard_sizes[]
    planner:shard:{study_id}:{idx}     dict — per-shard MAP result
    planner:reduce_embed:{study_id}    dict — embed stats
    planner:reduce_umap:{study_id}     dict — PCA/UMAP stats
    planner:reduce_k:{study_id}        dict — k_meta, k_volume, k_target, final_k
    planner:reduce_kmeans:{study_id}   dict — sweep[], best_k, sizes[]
    planner:reduce_thin:{study_id}     dict — thin_merge_log[]
    planner:reduce_split:{study_id}    dict — split_log[]
    planner:chapter_coherence:{id}     dict — Ch02 detector output
    planner:validation:{study_id}      dict — warnings, severity
    planner:coverage:{study_id}        dict — orphans, hallucinated

TTL

  2h per key. Long enough for a study to complete and an operator to
  inspect for several minutes after.

CONSUMED BY

  - `routers/v1/knowledge/distiller.py::get_planner_observability` (read all keys, merge into one snapshot)
  - `apps/fasthtml/routes/kd.py` planner observability page (poll every 2s)
"""
import json
import logging
import os
import time
from typing import Any, Optional


logger = logging.getLogger(__name__)


_KEY_PREFIX = "coelhonexus:knowledge:planner:"
_KEY_TTL_S = 7200


class PlannerProgress:
    """
    Per-study planner progress reporter. Each method targets one sub-step
    key. No-op when study_id is None (legacy callers stay unaffected).
    """

    def __init__(self, study_id: Optional[str]):
        self.study_id = study_id
        self._redis = None  # lazy init; False = gave up
        self._t_start = time.time()
        self._phase = "init"
        self._error: Optional[str] = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _key(self, suffix: str) -> str:
        return f"{_KEY_PREFIX}{suffix}:{self.study_id}"

    def _per_idx_key(self, suffix: str, idx: int) -> str:
        return f"{_KEY_PREFIX}{suffix}:{self.study_id}:{idx}"

    async def _get_redis(self):
        if self.study_id is None:
            return None
        if self._redis is False:
            return None
        if self._redis is None:
            try:
                import redis.asyncio as redis_aio
                host = os.environ.get("REDIS_HOST", "redis-master.redis.svc.cluster.local")
                port = os.environ.get("REDIS_PORT", "6379")
                password = os.environ.get("REDIS_PASSWORD", "")
                url = (
                    f"redis://:{password}@{host}:{port}"
                    if password else f"redis://{host}:{port}"
                )
                self._redis = redis_aio.from_url(
                    url, socket_connect_timeout = 3.0, socket_timeout = 5.0,
                )
            except Exception as e:
                logger.warning(f"[planner-progress] Redis init failed: {e}")
                self._redis = False
                return None
        return self._redis

    async def _write(self, key: str, payload: dict) -> None:
        r = await self._get_redis()
        if r is None:
            return
        try:
            payload["_recorded_at"] = time.time()
            await r.set(key, json.dumps(payload), ex = _KEY_TTL_S)
        except Exception as e:
            logger.info(f"[planner-progress] write {key} skipped: {e}")

    # ------------------------------------------------------------------
    # Status (called at phase boundaries)
    # ------------------------------------------------------------------
    async def set_phase(self, phase: str, error: Optional[str] = None) -> None:
        """
        Update the top-level phase pointer. Phases:
        init → corpus_load → off_topic → dedup → cache_lookup →
        shard_map → reduce → validate → done | failed.
        """
        self._phase = phase
        if error:
            self._error = error
        elapsed_ms = int((time.time() - self._t_start) * 1000)
        await self._write(self._key("status"), {
            "phase": phase,
            "elapsed_ms": elapsed_ms,
            "error_msg": self._error,
        })

    # ------------------------------------------------------------------
    # 2.1 corpus_load
    # ------------------------------------------------------------------
    async def record_corpus_load(
        self,
        *,
        total_files: int,
        total_bytes: int,
        min_bytes: int,
        max_bytes: int,
        median_bytes: int,
        load_ms: int,
    ) -> None:
        await self._write(self._key("corpus_load"), {
            "total_files": total_files,
            "total_bytes": total_bytes,
            "min_bytes": min_bytes,
            "max_bytes": max_bytes,
            "median_bytes": median_bytes,
            "load_ms": load_ms,
        })

    # ------------------------------------------------------------------
    # 2.2 off_topic_filter
    # ------------------------------------------------------------------
    async def record_off_topic(
        self,
        *,
        framework: str,
        threshold: float,
        kept: int,
        dropped: int,
        per_file_cosines: list[tuple[str, float, bool]],
        domain_coherence: Optional[float] = None,
        embedding_provider: Optional[str] = None,
        elapsed_ms: Optional[int] = None,
    ) -> None:
        """
        per_file_cosines: list of (slug, cosine, kept_bool). The page
        renders this as a sortable table — operator can spot boundary cases
        (cosine near threshold) and false-positive drops.

        domain_coherence: mean cosine of kept files to their centroid. Low
        domain_coherence (e.g. <0.5) means the kept set is itself scattered
        — worth investigating before downstream MAP/REDUCE.
        """
        await self._write(self._key("off_topic"), {
            "framework": framework,
            "threshold": threshold,
            "kept": kept,
            "dropped": dropped,
            "per_file_cosines": [
                {"slug": s, "cosine": float(c), "kept": bool(k)}
                for s, c, k in per_file_cosines
            ],
            "domain_coherence": domain_coherence,
            "embedding_provider": embedding_provider,
            "elapsed_ms": elapsed_ms,
        })

    # ------------------------------------------------------------------
    # 2.3 code_aware_dedup
    # ------------------------------------------------------------------
    async def record_dedup(
        self,
        *,
        threshold: float,
        pairs_checked: int,
        dropped: int,
        dedup_log: list[dict],
        elapsed_ms: Optional[int] = None,
    ) -> None:
        """
        dedup_log items: {slug_kept, slug_dropped, jaccard, code_match,
        len_kept, len_dropped}. Capped at 50 entries in the caller.
        """
        await self._write(self._key("dedup"), {
            "threshold": threshold,
            "pairs_checked": pairs_checked,
            "dropped": dropped,
            "dedup_log": dedup_log,
            "elapsed_ms": elapsed_ms,
        })

    # ------------------------------------------------------------------
    # 2.5 cache lookup
    # ------------------------------------------------------------------
    async def record_cache_lookup(
        self,
        *,
        manifest_hash: str,
        hit: bool,
        cached_at: Optional[str] = None,
        age_seconds: Optional[int] = None,
    ) -> None:
        await self._write(self._key("cache"), {
            "manifest_hash": manifest_hash,
            "hit": hit,
            "cached_at": cached_at,
            "age_seconds": age_seconds,
        })

    # ------------------------------------------------------------------
    # 2.6 shard creation
    # ------------------------------------------------------------------
    async def record_shards(
        self,
        *,
        total_shards: int,
        shard_sizes: list[int],
        shard_size_cap: int,
    ) -> None:
        await self._write(self._key("shards"), {
            "total_shards": total_shards,
            "shard_sizes": shard_sizes,
            "shard_size_cap": shard_size_cap,
        })

    # ------------------------------------------------------------------
    # 2.7 MAP per shard
    # ------------------------------------------------------------------
    async def record_shard_result(
        self,
        idx: int,
        *,
        path: str,                            # "strict_json" | "fallback_fc" | "catchall" | "timeout"
        n_input_slugs: int,
        n_clusters: int,
        n_unused: int,
        clusters: list[dict],                 # [{name, description, file_slugs, coherence?}]
        finish_reason: Optional[str] = None,
        truncated: bool = False,
        timeout_ms: Optional[int] = None,
        elapsed_ms: Optional[int] = None,
        fallback_audit: Optional[dict] = None,
        error_msg: Optional[str] = None,
    ) -> None:
        """
        One write per shard. The page renders a card per shard with the
        cluster list, path taken (strict vs fallback vs timeout), and
        coherence score per cluster (if provided).
        """
        await self._write(self._per_idx_key("shard", idx), {
            "idx": idx,
            "path": path,
            "n_input_slugs": n_input_slugs,
            "n_clusters": n_clusters,
            "n_unused": n_unused,
            "clusters": clusters,
            "finish_reason": finish_reason,
            "truncated": truncated,
            "timeout_ms": timeout_ms,
            "elapsed_ms": elapsed_ms,
            "fallback_audit": fallback_audit,
            "error_msg": error_msg,
        })

    # ------------------------------------------------------------------
    # 2.9 REDUCE sub-steps
    # ------------------------------------------------------------------
    async def record_reduce_embed(
        self,
        *,
        n_clusters: int,
        dimensions: int,
        provider: str,
        cache_hits: Optional[int] = None,
        elapsed_ms: Optional[int] = None,
    ) -> None:
        await self._write(self._key("reduce_embed"), {
            "n_clusters": n_clusters,
            "dimensions": dimensions,
            "provider": provider,
            "cache_hits": cache_hits,
            "elapsed_ms": elapsed_ms,
        })

    async def record_reduce_umap(
        self,
        *,
        pca_in_dim: Optional[int] = None,
        pca_out_dim: Optional[int] = None,
        pca_explained_variance: Optional[float] = None,
        umap_in_dim: int,
        umap_out_dim: int,
        n_neighbors: int,
        min_dist: float,
        elapsed_ms: Optional[int] = None,
    ) -> None:
        await self._write(self._key("reduce_umap"), {
            "pca_in_dim": pca_in_dim,
            "pca_out_dim": pca_out_dim,
            "pca_explained_variance": pca_explained_variance,
            "umap_in_dim": umap_in_dim,
            "umap_out_dim": umap_out_dim,
            "n_neighbors": n_neighbors,
            "min_dist": min_dist,
            "elapsed_ms": elapsed_ms,
        })

    async def record_reduce_k_selection(
        self,
        *,
        k_meta: int,
        k_volume: int,
        k_target: int,
        final_k: int,
        clamp_min: int,
        clamp_max: int,
    ) -> None:
        await self._write(self._key("reduce_k"), {
            "k_meta": k_meta,
            "k_volume": k_volume,
            "k_target": k_target,
            "final_k": final_k,
            "clamp_min": clamp_min,
            "clamp_max": clamp_max,
        })

    async def record_reduce_kmeans(
        self,
        *,
        sweep: list[dict],                    # [{k, ch, silhouette}]
        best_k: int,
        cluster_sizes: list[int],
        size_min: int,
        size_max: int,
        elapsed_ms: Optional[int] = None,
    ) -> None:
        await self._write(self._key("reduce_kmeans"), {
            "sweep": sweep,
            "best_k": best_k,
            "cluster_sizes": cluster_sizes,
            "size_min": size_min,
            "size_max": size_max,
            "elapsed_ms": elapsed_ms,
        })

    async def record_reduce_thin_merges(
        self,
        *,
        threshold_files: int,
        merges: list[dict],                   # [{thin_mid, fat_mid, files_moved, cosine}]
        elapsed_ms: Optional[int] = None,
    ) -> None:
        await self._write(self._key("reduce_thin"), {
            "threshold_files": threshold_files,
            "merges": merges,
            "elapsed_ms": elapsed_ms,
        })

    async def record_reduce_splits(
        self,
        *,
        file_cap_fraction: float,
        splits: list[dict],                   # [{orig_mid, sub_k, sub_buckets: [{files, coherence}]}]
        elapsed_ms: Optional[int] = None,
    ) -> None:
        await self._write(self._key("reduce_split"), {
            "file_cap_fraction": file_cap_fraction,
            "splits": splits,
            "elapsed_ms": elapsed_ms,
        })

    # ------------------------------------------------------------------
    # 2.9g + NEW Ch02 detector
    # ------------------------------------------------------------------
    async def record_chapter_coherence(
        self,
        *,
        chapters: list[dict],                 # [{number, title, n_files, coherence_score, low_coherence_files: [(slug, cos)]}]
        threshold_red: float,                 # below this = Ch02-class flag
        threshold_yellow: float,
        embedding_provider: Optional[str] = None,
        elapsed_ms: Optional[int] = None,
    ) -> None:
        """
        THE Ch02 mis-routing detector.

        For each final chapter the planner emitted:
          chapter_coherence = mean( cos(title_embedding, file_embedding)
                                    for file in chapter.assigned_files )

        Low coherence means the chapter title doesn't match the content of
        its assigned files. Ch02 in the Docker run shipped with title
        "Docker Account Management" but body files were Linux install
        procedures — that would have been red here at chapter-emission
        time, not buried in 1,300 pages of synthesized output.
        """
        await self._write(self._key("chapter_coherence"), {
            "chapters": chapters,
            "threshold_red": threshold_red,
            "threshold_yellow": threshold_yellow,
            "embedding_provider": embedding_provider,
            "elapsed_ms": elapsed_ms,
        })

    # ------------------------------------------------------------------
    # 2.11 validation + 2.12 coverage repair
    # ------------------------------------------------------------------
    async def record_validation(
        self,
        *,
        warnings: list[str],
        has_duplicates: bool,
        orphan_count: int,
        hallucinated_count: int,
        drop_rate: float,
        is_valid: bool,
    ) -> None:
        await self._write(self._key("validation"), {
            "warnings": warnings,
            "has_duplicates": has_duplicates,
            "orphan_count": orphan_count,
            "hallucinated_count": hallucinated_count,
            "drop_rate": drop_rate,
            "is_valid": is_valid,
        })

    async def record_coverage_repair(
        self,
        *,
        orphans_added: int,
        orphans_examples: list[str],
        hallucinated_dropped: int,
        chapters_affected: dict,              # {ch_num: int_dropped}
    ) -> None:
        await self._write(self._key("coverage"), {
            "orphans_added": orphans_added,
            "orphans_examples": orphans_examples,
            "hallucinated_dropped": hallucinated_dropped,
            "chapters_affected": chapters_affected,
        })

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    async def close(self) -> None:
        if self._redis and self._redis is not False:
            try:
                await self._redis.aclose()
            except Exception:
                pass
        self._redis = False


# =============================================================================
# Read-side helper — collect every key into one snapshot
# =============================================================================
async def read_planner_snapshot(redis_aio, study_id: str) -> dict:
    """
    Pull every planner sub-step key for `study_id` and return as a single
    dict. The FastAPI endpoint thin-wraps this; the FastHTML page polls
    every 2 s.

    Keys absent in Redis come back as None — the page treats None as
    "this sub-step hasn't run yet" and renders a pending placeholder.
    """
    if not study_id:
        return {}

    suffix_keys = [
        "status", "corpus_load", "off_topic", "dedup", "cache",
        "shards", "reduce_embed", "reduce_umap", "reduce_k",
        "reduce_kmeans", "reduce_thin", "reduce_split",
        "chapter_coherence", "validation", "coverage",
    ]
    snapshot: dict[str, Any] = {}
    for s in suffix_keys:
        key = f"{_KEY_PREFIX}{s}:{study_id}"
        try:
            raw = await redis_aio.get(key)
        except Exception:
            snapshot[s] = None
            continue
        if not raw:
            snapshot[s] = None
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            snapshot[s] = json.loads(raw)
        except Exception:
            snapshot[s] = None

    # Per-shard records — pattern scan for `planner:shard:{study_id}:{idx}`
    snapshot["shard_results"] = []
    try:
        pattern = f"{_KEY_PREFIX}shard:{study_id}:*"
        async for key in redis_aio.scan_iter(match = pattern, count = 200):
            try:
                raw = await redis_aio.get(key)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                if raw:
                    snapshot["shard_results"].append(json.loads(raw))
            except Exception:
                continue
        snapshot["shard_results"].sort(key = lambda d: d.get("idx", 0))
    except Exception as e:
        logger.info(f"[planner-progress] shard scan failed: {e}")

    return snapshot
