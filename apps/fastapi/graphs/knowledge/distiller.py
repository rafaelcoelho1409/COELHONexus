"""
Knowledge Distiller — LangGraph Pipeline (KnowledgeDistillerGraph class)

Class-based graph builder. Mirrors the organization of
graphs/youtube/adaptive.py and graphs/youtube/rag.py so every graph in the
project follows the same shape: methods for nodes + conditional edges, then
a build_*_graph(...) method that wires them into a StateGraph.

Each node takes the current state + dependencies (an LLM, a storage client)
and returns a partial state update. LangGraph merges the returned dict into
the main state dictionary automatically.

Storage: every read/write goes through MinIOStudyStorage. No local files.
Key prefix for each run: state["study_root"]
(e.g. "default/knowledge/fastapi-0.104.1-20260419T150000Z").

Helpers (reads, writes, regex scans, LLM-chain wrappers, formatting) live
in graphs/knowledge/helpers.py — mirrors the graphs/youtube/helpers.py
pattern so the main graph file stays focused on orchestration.

MODEL CONSISTENCY:
  We want all chapters of a single study to be synthesized by the SAME
  primary model when possible, for a unified voice across the material.
  Uncontrolled parallelism stresses the primary model's rate limits and
  forces chapters onto different fallbacks → inconsistent tone.

  Mitigation: a per-study `asyncio.Semaphore` caps concurrent
  synthesize_chapter workers. With K=2, typical NIM free-tier headroom
  (40 RPM per model) is plenty for the primary to serve every chapter's
  initial call without falling back. Langchain's `with_fallbacks` still
  applies per-chapter so a legitimately-down primary escalates cleanly.

Pipeline shape:
    START → ingest → planner → (Send fan-out to N workers)
          → synthesize_chapter [×N parallel, capped by semaphore]
            (results merged via operator.add)
          → critic → assembler → END
"""
import asyncio
import logging
from typing import Optional
from pydantic import ValidationError as PydanticValidationError
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from schemas.knowledge.agents import (
    ChapterPlan,
    ChapterSynthesis,
    CriticAssessment,
    Flashcard,
    GraderEvaluation,
    ShardLabels,
)
from schemas.knowledge.ingestion import DocsIngestionConfig
from schemas.knowledge.inputs import UserProfile
from schemas.knowledge.prompts import (
    CRITIC_PROMPT,
    CURATOR_PROMPT,
    PLANNER_PROMPT,
    SHARD_LABEL_PROMPT,
    build_tone_block,
)
from schemas.knowledge.state import KnowledgeDistillerState
from services.knowledge.cache import (
    StudyCache,
    canonical_profile_hash,
    compute_manifest_hash,
)
from services.knowledge.ingestion import ingest_framework_docs
from services.knowledge.storage import MinIOStudyStorage
from graphs.knowledge.helpers import (
    # Step 5
    _build_corpus_summary,
    _dedup_chapter_files,
    _filter_noise_files,
    _maybe_split_monolith,
    _read_raw_prefix,
    _validate_plan,
    _write_plan_json,
    # Step 6
    _assemble_chapter_markdown,
    _audit_sentinel_roundtrip,
    _audit_structured_output_refs,
    _format_preservation_feedback,
    _format_structured_output_feedback,
    _generate_adjustment,
    _grade_attempt,
    _invoke_structured_with_fallback,
    _load_chapter_files,
    _restore_code_blocks,
    _synthesize_attempt,
    _user_profile_summary,
    # OP-31 (2026-04-25) — sentinel for chapter-level zero-citation gate
    _ZERO_CITATIONS_MARKER,
    _vault_code_blocks,
    _write_chapter_artifacts,
    # Step 7 + deterministic linter + glossary (new)
    _build_chapter_bundles,
    _deterministic_linter,
    _extract_glossary_terms,
    _load_all_chapters,
    _load_available_slugs,
    _scan_citations,
    _scan_hallucinated_fences,
    # Step 8
    _build_chapter_summaries,
    _build_debt_md,
    _call_assembler_llm,
    _load_chapter_previews,
    _log_episodic_memory,
    # Step 9
    _write_manifest_json,
)


logger = logging.getLogger(__name__)


# =============================================================================
# Module-level constants
# =============================================================================
# Self-Refine iteration budget. 0-indexed: attempt 0 is the first try, 1/2 are
# retries. Matches the bound recommended by Madaan et al. in the Self-Refine
# paper. Used as the FALLBACK when adaptive sizing is bypassed.
MAX_SELF_REFINE_ITERATIONS = 5


# OP-18 (2026-04-25) — adaptive iteration budget per chapter.
# Run-11 evidence: ch04 hit 14 issues at iter 0 (very clean), wasted 4 more
# iters. ch01 hit 83 issues at iter 0, plateaued by iter 4. Easy chapters
# don't need many refines; hard ones need more headroom. Pick budget by
# vault-hash count, which correlates with chapter complexity.
def _adaptive_iter_budget(n_vault_hashes: int) -> int:
    """
    Tier-bucketed iteration budget. Bigger chapters get more iterations.
      - ≤30 hashes  → 3 iters (typical clean chapter; converges fast)
      - 31-80 hashes → 5 iters (current default; room for one regression+recovery)
      - >80 hashes  → 7 iters (hard chapters need more refine budget)
    Hard cap at 7 — beyond that the LLM diverges (Self-Refine paper §3.3).
    """
    if n_vault_hashes <= 30:
        return 3
    if n_vault_hashes <= 80:
        return 5
    return 7


class KnowledgeDistillerGraph:
    def __init__(self):
        pass

    # =========================================================================
    # Node: Ingest (cache-aware)
    # =========================================================================
    async def ingest(
        self,
        state: KnowledgeDistillerState,
        storage: MinIOStudyStorage,
        cache: StudyCache) -> dict:
        """
        Graph entry point. Checks the ingestion cache first:
          - CACHE HIT: copies `_cache/ingestion/{framework}/{version}/` into
            `<study_root>/research/raw/` — skips the 60-500s crawl entirely.
          - CACHE MISS: runs the tiered crawl, writes both `study_root` AND
            the cache so future runs with the same (framework, version) are fast.

        Freshness:
          - `version="latest"` entries expire after 14 days
          - pinned versions are immutable (cache never expires)

        Required state fields: framework, docs_url, study_root, version.
        """
        framework = state["framework"]
        version = state.get("version") or "latest"
        study_root = state["study_root"]
        docs_url = state.get("docs_url")

        # 1) Cache lookup
        hit = await cache.get_ingestion(framework, version)
        if hit is not None:
            await cache.copy_ingestion_to_study(framework, version, study_root)
            logger.info(
                f"[ingest] CACHE HIT framework={framework} version={version} "
                f"files={len(hit.raw_keys)} cached_at={hit.cached_at}"
            )
            return {
                "raw_files": [k.rsplit("/", 1)[-1].removesuffix(".md") for k in hit.raw_keys],
                "manifest": hit.manifest,
                "ingest_tier_used": f"{hit.tier_used}+cache",
                "current_phase": "plan",
            }

        # 2) Cache miss — run the full tiered ingest
        if not docs_url:
            raise ValueError(
                "state['docs_url'] is required for ingestion. The FastAPI router "
                "is responsible for resolving it from the framework name "
                "when the user doesn't supply one."
            )
        cfg = DocsIngestionConfig(
            framework = framework,
            version = version,
            docs_url = docs_url,
            language = state.get("language"),
            study_root = study_root,
            # study_id enables per-page progress reporting to Redis via
            # IngestProgress → /studies/{id}/stream SSE events. None on
            # legacy graph invocations that didn't carry it.
            study_id = state.get("study_id"),
            # Forward resolver hints — dispatcher in ingestion.py uses
            # these to pick the right tier; None values fall through to
            # Tier 4 (Crawl4AI Playwright) for backward compat.
            tier = state.get("tier"),
            github_discover = state.get("github_discover"),
            github_org = state.get("github_org"),
            github_repo = state.get("github_repo"),
            github_default_branch = state.get("github_default_branch"),
        )
        # Pass cache into ingest so it can (a) skip URLs that a previous
        # attempt already cached (resume), and (b) tee every successful
        # page to the cache as it arrives (stream). A worker crash or
        # cancel mid-crawl leaves a partial cache; the next run picks up
        # exactly where it stopped.
        result = await ingest_framework_docs(cfg, storage, cache = cache)
        # Persist manifest next to raw/ in study_root
        await _write_manifest_json(storage, study_root, result.manifest)
        logger.info(
            f"[ingest] tier={result.tier_used} files={result.total_files} "
            f"bytes={result.total_bytes}"
        )

        # 3) Finalize the cache entry. Raw files were already written
        #    per-page via `save_ingested_page` during streaming; this step
        #    just lays down the manifest.json + _state.json completeness
        #    marker so future `get_ingestion` calls hit.
        try:
            slugs = [e.slug for e in result.manifest]
            await cache.finalize_ingestion(
                framework = framework,
                version = version,
                study_root = study_root,
                manifest = [e.model_dump() for e in result.manifest],
                slugs = slugs,
            )
        except Exception as e:
            logger.warning(f"[ingest] cache finalize failed (continuing): {e}")

        return {
            "raw_files": [e.slug for e in result.manifest],
            "manifest": [e.model_dump() for e in result.manifest],
            "ingest_tier_used": result.tier_used,
            "current_phase": "plan",
        }

    # =========================================================================
    # Node: Planner
    # =========================================================================
    async def planner(
        self,
        state: KnowledgeDistillerState,
        llm: ChatOpenAI,
        storage: MinIOStudyStorage,
        cache: StudyCache) -> dict:
        """
        Decompose the ingested corpus into 4-12 chapters — cache-aware.

        Cache rules:
          - HIT: `_cache/planning/{framework}/{version}/plan.json` exists AND
            its stored `manifest_hash` matches the current corpus's sorted
            slug hash → copy plan.json into study_root, skip LLM call.
          - MISS: run the planner, then write plan.json to the cache with
            the current manifest_hash tied to it.

        If raw/ changed (Tier 1 monolith split, corpus evolved, etc.), the
        manifest_hash differs and the cache is invalidated automatically.
        """
        study_root = state["study_root"]
        framework = state["framework"]
        version = state.get("version") or "latest"

        # 1) Read corpus from MinIO, normalize Tier-1 monolith if present
        entries = await _read_raw_prefix(storage, study_root)
        if not entries:
            raise FileNotFoundError(f"research/raw/ is empty at prefix {study_root!r}")
        entries = await _maybe_split_monolith(storage, study_root, entries)
        # Tier 4 #17 (2026-04-24) — noise pre-filter before MAP.
        # Drops obvious non-pedagogical slugs (changelog / release-notes /
        # stubs with <200 chars of prose / files with ~0 code-to-prose ratio)
        # BEFORE MAP shards burn LLM calls on them. Typical effect: 5-15%
        # fewer shards, zero pedagogical loss.
        before_filter = len(entries)
        entries = _filter_noise_files(entries)
        filtered = before_filter - len(entries)
        if filtered > 0:
            logger.info(
                f"[planner] noise pre-filter dropped {filtered}/{before_filter} "
                f"entries ({filtered * 100 // max(1, before_filter)}%)"
            )
        # Tier 2 #6 (2026-04-24) — code-aware near-dup filter.
        # Drops files that share >85% prose AND have identical code block
        # sets (keeps the longer of the pair). A single code-block difference
        # means the pair is NOT a dup — protects tutorial-vs-reference pairs
        # with meaningful code variations. Defensive fallback on any error.
        before_dedup = len(entries)
        entries = _dedup_chapter_files(entries)
        deduped = before_dedup - len(entries)
        if deduped > 0:
            logger.info(
                f"[planner] code-aware dedup dropped {deduped}/{before_dedup} "
                f"near-duplicate entries ({deduped * 100 // max(1, before_dedup)}%)"
            )
        slugs = [slug for slug, _ in entries]
        manifest_hash = compute_manifest_hash(slugs)

        # 2) Cache lookup — must match both (framework, version) AND manifest hash
        hit = await cache.get_plan(framework, version, manifest_hash)
        if hit is not None:
            await cache.copy_plan_to_study(framework, version, study_root)
            # Reload the plan we just copied so we can return its chapters
            plan_json_key = f"{study_root}/research/plan.json"
            plan_json = await storage.read_text(plan_json_key)
            import json as _json
            plan_data = _json.loads(plan_json)
            chapters = [ChapterPlan(**c) for c in (plan_data.get("chapters") or [])]
            logger.info(
                f"[planner] CACHE HIT framework={framework} version={version} "
                f"chapters={len(chapters)} cached_at={hit.cached_at}"
            )
            return {
                "plan": chapters,
                "current_phase": "synthesize",
            }

        # 3) Cache miss — MAP-REDUCE planner (2026-04-21 research)
        #
        # Single-prompt planner fails on large corpora:
        #   - Groq llama-3.3-70b-versatile free tier: 12K TPM → 413 on 994-file
        #     prompts (observed arxiv-citable root cause: continuedev/continue#10218)
        #   - NIM reasoning models: proxy timeout (504) on very long prompts
        #   - Output truncation on large structured responses: 77% of files
        #     silently missing from the plan (observed 2026-04-21 run)
        #
        # Map-reduce dissolves all three:
        #   MAP   — shard corpus into chunks of ≤40 files, run shard-labelers
        #           in parallel (asyncio.gather). Each shard prompt is ~5K
        #           tokens, well under 12K TPM.
        #   REDUCE — a second small call merges N shard results into
        #           4-12 chapters. Sees only cluster summaries (~5K tokens),
        #           not the raw corpus previews.
        #
        # Reference: docs/KNOWLEDGE-DISTILLER-PLANNER-FIXES.md §Fix #1
        # (LangChain Academy §7.1 map-reduce, Medium map-reduce Send API).
        total_chars = sum(len(c) for _, c in entries)
        logger.info(
            f"[planner] {len(entries)} files, {total_chars} total chars (cache MISS)"
        )
        _SHARD_SIZE = 40
        shards = [
            entries[i : i + _SHARD_SIZE]
            for i in range(0, len(entries), _SHARD_SIZE)
        ]
        logger.info(
            f"[planner] map-reduce: {len(shards)} shards of ≤{_SHARD_SIZE} files each"
        )

        # ------ MAP pass — parallel shard-labelers ---------------------------
        # Strict JSON-Schema enum decoding (Planner-fixes doc Fix #2):
        # Build a schema where `file_slugs` and `unused_shard_slugs` are
        # enum-constrained to THIS shard's slugs only. OpenAI strict mode
        # caps enum at 1,000 values — shard of ~40 is well under. Prevents
        # hallucinations at DECODE time (not post-hoc). Groq supports strict
        # mode per console.groq.com/docs/structured-outputs; on NIM the
        # support is patchier — if strict fails we fall back to
        # `with_structured_output` (function_calling) + post-hoc drop.
        def _build_strict_schema(shard_slug_list: list[str]) -> dict:
            return {
                "type": "object",
                "properties": {
                    "clusters": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "properties": {
                                "cluster_name": {"type": "string"},
                                "description": {"type": "string"},
                                "file_slugs": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {"type": "string", "enum": shard_slug_list},
                                },
                            },
                            "required": ["cluster_name", "description", "file_slugs"],
                            "additionalProperties": False,
                        },
                    },
                    "unused_shard_slugs": {
                        "type": "array",
                        "items": {"type": "string", "enum": shard_slug_list},
                    },
                },
                "required": ["clusters", "unused_shard_slugs"],
                "additionalProperties": False,
            }

        async def _label_shard(shard_entries: list[tuple[str, str]], shard_idx: int) -> ShardLabels:
            shard_summary = _build_corpus_summary(shard_entries)
            shard_slugs = [slug for slug, _ in shard_entries]
            # Try strict JSON-Schema first (decode-time enum, no hallucinations possible)
            parsed: Optional[ShardLabels] = None
            raw_msg = None
            strict_err: Optional[Exception] = None
            try:
                strict_llm = llm.bind(response_format = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "ShardLabels",
                        "schema": _build_strict_schema(shard_slugs),
                        "strict": True,
                    },
                })
                strict_chain = SHARD_LABEL_PROMPT | strict_llm
                raw_msg = await strict_chain.ainvoke({
                    "framework": framework,
                    "shard_summary": shard_summary,
                })
                # raw_msg is an AIMessage; content is the strict JSON
                content = getattr(raw_msg, "content", "") or ""
                if content:
                    parsed = ShardLabels.model_validate_json(content)
            except Exception as e:
                strict_err = e
                logger.info(
                    f"[planner][shard {shard_idx}/{len(shards)}] strict JSON-Schema path "
                    f"unavailable ({type(e).__name__}: {str(e)[:80]}); falling back to "
                    f"function_calling with post-hoc filter"
                )
            # Fallback path: standard with_structured_output + post-hoc slug filter
            if parsed is None:
                shard_chain = SHARD_LABEL_PROMPT | llm.with_structured_output(
                    ShardLabels, method = "function_calling", include_raw = True,
                )
                try:
                    resp = await shard_chain.ainvoke({
                        "framework": framework,
                        "shard_summary": shard_summary,
                    })
                except Exception as e:
                    logger.warning(
                        f"[planner][shard {shard_idx}/{len(shards)}] failed ({e}); "
                        f"using catch-all cluster"
                    )
                    from schemas.knowledge.agents import ShardCluster
                    return ShardLabels(
                        clusters = [ShardCluster(
                            cluster_name = f"Shard {shard_idx} (unlabeled)",
                            description = "Fallback cluster after shard labeler failure",
                            file_slugs = shard_slugs,
                        )],
                        unused_shard_slugs = [],
                    )
                raw_msg = resp.get("raw")
                parsed = resp.get("parsed")
                parsing_error = resp.get("parsing_error")
                if parsing_error is not None or parsed is None:
                    logger.warning(
                        f"[planner][shard {shard_idx}/{len(shards)}] parse failed "
                        f"({parsing_error}); using catch-all cluster"
                    )
                    from schemas.knowledge.agents import ShardCluster
                    return ShardLabels(
                        clusters = [ShardCluster(
                            cluster_name = f"Shard {shard_idx} (parse-fallback)",
                            description = "Fallback after parse error",
                            file_slugs = shard_slugs,
                        )],
                        unused_shard_slugs = [],
                    )
            elif strict_err is None:
                logger.info(
                    f"[planner][shard {shard_idx}/{len(shards)}] strict JSON-Schema OK "
                    f"({len(parsed.clusters)} clusters, "
                    f"{len(parsed.unused_shard_slugs)} unused)"
                )
            # Coverage invariant at shard level: every input slug must appear
            # somewhere (assigned to a cluster OR marked unused). If the LLM
            # dropped slugs, auto-append them to unused_shard_slugs.
            shard_assigned = set()
            for c in parsed.clusters:
                # Drop hallucinated slugs (LLM invented a slug not in this shard)
                c.file_slugs = [s for s in c.file_slugs if s in shard_slugs]
                shard_assigned.update(c.file_slugs)
            shard_unused_set = set(parsed.unused_shard_slugs or [])
            missing = set(shard_slugs) - shard_assigned - shard_unused_set
            if missing:
                parsed.unused_shard_slugs = list(shard_unused_set | missing)
                logger.info(
                    f"[planner][shard {shard_idx}/{len(shards)}] auto-parked "
                    f"{len(missing)} dropped slug(s) to unused_shard_slugs"
                )
            # Truncation guard at shard level
            if raw_msg is not None and hasattr(raw_msg, "response_metadata"):
                if (raw_msg.response_metadata or {}).get("finish_reason") == "length":
                    logger.warning(
                        f"[planner][shard {shard_idx}/{len(shards)}] output truncated "
                        f"(finish_reason=length); result may be incomplete"
                    )
            return parsed

        import asyncio as _asyncio
        # Tier 1 #4 (2026-04-23) — MAP inter-shard semaphore.
        # Unbounded asyncio.gather(103 shards) saturates any free-tier
        # RPM budget and produces 429 storms (Run-6 observed 172 rate-
        # limit retries in one study).
        #
        # Evolution:
        #   - Tier 1 #4 (2026-04-23): sem=30. OK for Run-5/6/7.
        #   - OP-1 (2026-04-24, post-Run-8): sem=15 to reduce burst-races
        #     on rate-limited providers. Side effect in Run-9: individual
        #     straggler shards (5-9 min each) pile up under the tight slot
        #     count → MAP took 30 min vs Run-8's 10.
        #   - OP-5 (2026-04-24, post-Run-9): sem=22 middle ground + a
        #     per-shard 180s time-box inside the semaphore slot. Prevents
        #     both burst-races (via bounded concurrency) AND long-tail
        #     stragglers (via wait_for). A timed-out shard returns empty
        #     ShardLabels so the overall pipeline advances — losing ~30
        #     slugs is better than blowing 10 min on one stuck cascade.
        MAP_SHARD_SEMAPHORE = _asyncio.Semaphore(22)
        _MAP_SHARD_TIMEOUT_SECONDS = 180  # OP-5

        async def _label_shard_bounded(shard, shard_idx):
            async with MAP_SHARD_SEMAPHORE:
                try:
                    return await _asyncio.wait_for(
                        _label_shard(shard, shard_idx),
                        timeout = _MAP_SHARD_TIMEOUT_SECONDS,
                    )
                except _asyncio.TimeoutError:
                    logger.warning(
                        f"[planner][shard {shard_idx}/{len(shards)}] "
                        f"exceeded {_MAP_SHARD_TIMEOUT_SECONDS}s time-box "
                        f"— emitting synthetic timed-out cluster so MAP "
                        f"advances (slugs will park to unused via REDUCE)"
                    )
                    # Run-11 RCA (2026-04-24 late): original return used
                    # clusters=[] but ShardLabels.clusters has min_length=1.
                    # Pydantic rejects empty list → whole task fails. Instead
                    # emit ONE synthetic "timed-out" cluster carrying the
                    # shard's slugs so the schema validates. REDUCE's Clio
                    # v2 re-clustering treats this cluster like any other
                    # micro-cluster; if it's semantically incoherent, KMeans
                    # scatters its files across real clusters anyway.
                    from schemas.knowledge.agents import ShardCluster
                    shard_slugs = [slug for slug, _ in shard]
                    return ShardLabels(
                        clusters = [ShardCluster(
                            cluster_name = f"Shard {shard_idx} (timed out)",
                            description = (
                                f"Shard exceeded {_MAP_SHARD_TIMEOUT_SECONDS}s "
                                "labeler time-box; slugs forwarded for REDUCE "
                                "to re-cluster semantically."
                            ),
                            file_slugs = shard_slugs,
                        )],
                        unused_shard_slugs = [],
                    )

        _shard_start = _asyncio.get_event_loop().time()
        shard_results: list[ShardLabels] = await _asyncio.gather(
            *(_label_shard_bounded(s, i + 1) for i, s in enumerate(shards)),
        )
        _shard_elapsed = _asyncio.get_event_loop().time() - _shard_start
        total_clusters = sum(len(r.clusters) for r in shard_results)
        total_shard_unused = sum(len(r.unused_shard_slugs) for r in shard_results)
        logger.info(
            f"[planner] MAP complete: {len(shards)} shards → {total_clusters} "
            f"micro-clusters + {total_shard_unused} shard-unused slugs "
            f"in {_shard_elapsed:.1f}s"
        )

        # ------ REDUCE pass — Clio pattern (embed + k-means + label + order) -
        # The previous single-shot CHAPTER_REDUCE_PROMPT call failed reliably
        # on large corpora (observed 2026-04-22 on 300 micro-clusters):
        #   - NIM 504 gateway timeout after 300s on every reasoning model
        #   - Groq 413 TPM rate-limit on every model except llama-4-scout
        # Both are structural — a 25K-token prompt simply can't fit through
        # free-tier NIM's gateway window or Groq's 6K-12K TPM caps.
        #
        # The Clio pattern (Anthropic, arxiv 2412.13678) decouples grouping
        # (embeddings + k-means, deterministic, zero LLM tokens) from naming
        # (one small LLM call per meta-cluster, ~3K tokens each, parallel).
        # No LLM call ever sees all 300 clusters; biggest prompt is ~3K tokens
        # — safely under every provider's constraint.
        #
        # See graphs/knowledge/reduce_cluster.py for the full implementation.
        shard_unused_all: list[str] = []
        for sr in shard_results:
            shard_unused_all.extend(sr.unused_shard_slugs)
        from graphs.knowledge.reduce_cluster import embed_and_cluster_reduce
        try:
            plan = await embed_and_cluster_reduce(
                shard_results = shard_results,
                shard_unused_all = shard_unused_all,
                framework = framework,
                llm = llm,
            )
        except Exception as e:
            raise RuntimeError(f"Planner REDUCE (Clio pattern) failed: {e}") from e
        logger.info(
            f"[planner] REDUCE complete: {len(plan.chapters)} chapters + "
            f"{len(plan.unused_files)} unused_files (reasoning: {plan.reasoning[:120]})"
        )

        # 4) Validate (log-only — critic node is the formal gate)
        available = {slug for slug, _ in entries}
        for warning in _validate_plan(plan, available):
            logger.warning(f"[planner] plan validation: {warning}")

        # 4b) Coverage invariant repair (Planner-fixes doc Fix #4):
        # After validation, GUARANTEE that every corpus slug is accounted for
        # (either in an assigned_files list, or in unused_files). Without this,
        # observed runs had 77% of the corpus silently missing from the plan —
        # downstream synthesizers got thin context.
        #
        # Repair strategy:
        #   - Orphans (in corpus, missing from plan) → auto-append to
        #     unused_files with a synthetic reason. Conservative: we don't
        #     know WHICH chapter they fit, so "unused" is the safe default.
        #     The critic + assembler still see them in DEBT.md tracking.
        #   - Hallucinated slugs (in plan's assigned_files, NOT in corpus)
        #     → drop them from assigned_files. Otherwise downstream synth
        #     tries to read a non-existent file and fails.
        assigned_set: set[str] = set()
        for ch in plan.chapters:
            assigned_set.update(ch.assigned_files)
        unused_slugs_set: set[str] = {u.slug for u in (plan.unused_files or [])}
        accounted = assigned_set | unused_slugs_set
        orphans = available - accounted
        hallucinated = (assigned_set | unused_slugs_set) - available
        if orphans:
            from schemas.knowledge.agents import UnusedFile as _UnusedFile
            plan.unused_files = list(plan.unused_files or []) + [
                _UnusedFile(slug = s, reason = "planner did not label; auto-parked in unused")
                for s in sorted(orphans)
            ]
            logger.info(
                f"[planner] coverage repair: auto-added {len(orphans)} orphan slugs "
                f"to unused_files (sample: {sorted(orphans)[:3]})"
            )
        if hallucinated:
            # Drop hallucinated slugs from every chapter's assigned_files
            _halluc_lower_aware = hallucinated  # set, O(1) lookups
            drop_count = 0
            for ch in plan.chapters:
                before = len(ch.assigned_files)
                ch.assigned_files = [s for s in ch.assigned_files if s not in _halluc_lower_aware]
                drop_count += before - len(ch.assigned_files)
            # Drop from unused_files too
            if plan.unused_files:
                plan.unused_files = [
                    u for u in plan.unused_files if u.slug not in _halluc_lower_aware
                ]
            logger.info(
                f"[planner] coverage repair: dropped {drop_count} hallucinated slug "
                f"references across chapters (sample: {sorted(hallucinated)[:3]})"
            )

        # 5) Persist plan.json to study_root
        plan_key = await _write_plan_json(storage, study_root, plan)
        logger.info(
            f"[planner] wrote {len(plan.chapters)}-chapter plan to {plan_key} — "
            f"reasoning: {plan.reasoning[:80]}"
        )

        # 6) Populate cache (best-effort)
        try:
            await cache.set_plan(framework, version, study_root, manifest_hash)
        except Exception as e:
            logger.warning(f"[planner] cache write failed (continuing): {e}")

        return {
            "plan": list(plan.chapters),
            "current_phase": "synthesize",
        }

    # =========================================================================
    # Node: Synthesize one chapter with Self-Refine
    # =========================================================================
    async def synthesize_chapter(
        self,
        payload: dict,
        llm: ChatOpenAI,
        storage: MinIOStudyStorage,
        cache: StudyCache) -> dict:
        """
        Synthesize one chapter with a bounded Self-Refine loop (max 3 attempts).

        Cache-aware: checks for a previously-accepted chapter with the same
        (framework, version, profile_hash, chapter_num, title, assigned_files).
        Cache hit → copy artifacts forward, skip the entire Self-Refine loop.
        Cache miss → run synthesis; on accept (above-threshold only), write to cache.

        Invoked by LangGraph Send() — receives a per-worker `payload` dict, not
        the full KnowledgeDistillerState. Each worker has isolated context so
        chapter N's iterations don't pollute chapter M's synthesis.

        Self-Refine decision table (per iteration):
            action='accept' OR weighted_score >= threshold → accept, break
            action='regenerate'                            → drop adjustments, retry
            action='refine'                                → append adjustment, retry
            iteration == MAX-1 without accept              → accept best effort + DEBT flag

        Payload shape (set by the fan-out closure):
            {
                "chapter": ChapterPlan,         # this worker's assignment
                "framework": str,
                "user_profile": UserProfile,
                "study_root": str,              # MinIO key prefix for this run
            }

        Returns a reducer-merged state update:
            {"synthesis_results": [ChapterResult_dict]}

        Side effects: 3 MinIO objects per chapter (README.md, challenges.md,
        flashcards.json) under <study_root>/chapterNN/.

        Raises:
            RuntimeError: synthesis or grader LLM call failed irrecoverably.
        """
        chapter: ChapterPlan = payload["chapter"]
        framework: str = payload["framework"]
        user_profile: UserProfile = payload["user_profile"]
        study_root: str = payload["study_root"]
        version: str = payload.get("version") or "latest"
        profile_hash: str = payload["profile_hash"]

        # 0) Cache lookup — chapter identity = (framework, version, profile_hash,
        #    chapter_num, title, assigned_files). Title/assigned_files tie the
        #    cached chapter to this specific planner output.
        hit = await cache.get_chapter(
            framework = framework,
            version = version,
            profile_hash = profile_hash,
            chapter_num = chapter.number,
            chapter_title = chapter.title,
            assigned_files = chapter.assigned_files,
        )
        if hit is not None:
            result = await cache.copy_chapter_to_study(
                framework = framework,
                version = version,
                profile_hash = profile_hash,
                chapter_num = chapter.number,
                study_root = study_root,
            )
            result["score"] = hit.score
            result["iterations"] = hit.iterations
            logger.info(
                f"[synth][ch{chapter.number:02d}] CACHE HIT — "
                f"score={hit.score:.2f} iterations={hit.iterations} cached_at={hit.cached_at}"
            )
            return {"synthesis_results": [result]}

        # 1) Cache miss — run the Self-Refine loop
        #
        # KEEP-BEST + EARLY-STOP on regression (2026-04-21 research):
        # Intrinsic self-correction without oracle feedback frequently regresses
        # — Huang et al. 2024 ICLR (arxiv 2310.01798v2 §3.3) measured GPT-4 on
        # GSM8K dropping 91.5 → 88.0 after one refine round. The original code
        # committed the LAST iteration unconditionally, which meant a bad
        # iter-N would overwrite a good iter-0. Observed in run 2026-04-21:
        # ch04 went 0.80 → 0.75 → 0.78 across iters 0-2.
        #
        # Mitigation:
        #   1. Track the argmax iteration (best_synthesis, best_eval) via score
        #   2. Commit best at the end (not last)
        #   3. Early-stop when score[i] < score[i-1] - 0.01 — further rounds
        #      compound drift (Huang §3.3); no point burning API budget
        #
        # References:
        #   - Huang et al. 2024, arxiv 2310.01798v2 §3.3
        #   - Kamoi et al. 2024, arxiv 2406.01297v3 §7 (intrinsic bottleneck)
        #   - PDR arxiv 2510.01123 (argmax over iterations, not last)
        tone_block = build_tone_block(user_profile)
        files_content_raw = await _load_chapter_files(
            storage, study_root, chapter.assigned_files,
            chapter_goal = chapter.goal,  # Tier 1 #1 BM25 ranking
        )
        # Tier 0a: vault fenced code blocks before synthesis so the LLM
        # cannot paraphrase, elide, reformat, or rename them. Tier 0c: the
        # integrity check after each attempt gates the grader — preservation
        # must be 100% to proceed; otherwise the iteration is a forced refine
        # with targeted per-sentinel feedback.
        files_content, code_vault = _vault_code_blocks(files_content_raw)
        logger.info(
            f"[synth][ch{chapter.number:02d}] vaulted {len(code_vault)} code "
            f"block(s); prompt is {len(files_content)} chars after vault "
            f"(was {len(files_content_raw)} raw)"
        )
        adjustments: list[str] = []
        history: list[tuple[ChapterSynthesis, GraderEvaluation]] = []  # graded iterations only
        best_synthesis: ChapterSynthesis | None = None
        best_eval: GraderEvaluation | None = None
        resume_from_iter: int = 0  # default cold start
        _REGRESSION_EPSILON = 0.01  # tolerance for grader noise

        # OP-12 (2026-04-24, post-Run-9) — commit-best-seen always.
        # Track the LEAST-BAD audit-failed iteration so we can commit it as
        # best-effort if the Self-Refine loop exhausts its budget without
        # any graded iter. Run-9 sentinel'd 6 chapters that each had a
        # near-clean audit-failed iter (ch07: 2 empty at iter 4; ch09:
        # 3 missing + 1 invented at iter 4; ch10: iter 1 clean except
        # 1 empty, regressed to 44 issues later). With OP-12 those become
        # "accepted below threshold with DEBT" instead of sentinels.
        best_audit_iter: dict | None = None   # {"output": ChapterOutput, "assembled": str, "n_issues": int, "iteration": int}
        # OP-7 (2026-04-24, post-Run-9) — audit-regression early-stop.
        # Track per-iter total audit issue count so we can break out of the
        # Self-Refine loop when the LLM starts drifting (iter N issues >
        # 3× iter N-1 issues). Without this, ch10 went iter 1 (1 empty) →
        # iter 2 (16 missing, an over-correction) → iter 3 (5 issues) →
        # iter 4 (44 issues) — budget wasted and best-seen lost.
        prev_n_issues: int | None = None
        _AUDIT_REGRESSION_FACTOR = 3

        # 0b) Tier 3 #13 extension (2026-04-24) — per-iteration partial cache.
        # Run-8 lost chapters that reached real grader scores (0.71 / 0.73)
        # at iter 1 but died on later-iter cascade timeouts. Partial cache
        # restores the best-so-far synthesis + accumulated adjustments +
        # iteration counter, so the next run resumes at iter N instead of
        # starting fresh at iter 0. Same identity check as full cache — a
        # replan that changes chapter title or assigned files invalidates.
        partial = await cache.get_chapter_partial(
            framework = framework,
            version = version,
            profile_hash = profile_hash,
            chapter_num = chapter.number,
            chapter_title = chapter.title,
            assigned_files = chapter.assigned_files,
        )
        if partial is not None:
            try:
                best_synthesis = ChapterSynthesis(
                    content = partial["best_synthesis_md"],
                    challenges = partial["best_challenges"],
                    flashcards = [
                        Flashcard(**f) for f in partial["best_flashcards_json"]
                    ],
                )
                best_eval = GraderEvaluation(**partial["best_evaluation_json"])
                adjustments = list(partial.get("adjustments") or [])
                resume_from_iter = int(partial["iteration_reached"])
                logger.info(
                    f"[synth][ch{chapter.number:02d}] PARTIAL CACHE HIT — "
                    f"resuming at iter {resume_from_iter} "
                    f"(best_score so far = {best_eval.weighted_score:.2f}, "
                    f"{len(adjustments)} adjustment(s) preserved)"
                )
            except Exception as e:
                logger.warning(
                    f"[synth][ch{chapter.number:02d}] partial cache decode "
                    f"failed ({e}); falling back to cold start"
                )
                best_synthesis = None
                best_eval = None
                adjustments = []
                resume_from_iter = 0
        # Tier 0d-6: isolate per-chapter synth failures. If the fallback chain
        # is truly exhausted (every synth model returned None / every grader
        # model failed) OR no graded iteration completes (every iter 0..N-1
        # fails the preservation gate), swallow the terminal RuntimeError and
        # emit a DEBT sentinel. Sibling chapters in the Send() fan-out must
        # keep running — a single chapter's LLM-side bad luck should not kill
        # the whole study.
        iteration = resume_from_iter - 1  # pre-loop sentinel
        # OP-18 (2026-04-25) — adaptive iteration budget by vault-hash count.
        # Easy chapters converge fast; hard ones need more iterations.
        _iter_budget = _adaptive_iter_budget(len(code_vault))
        if _iter_budget != MAX_SELF_REFINE_ITERATIONS:
            logger.info(
                f"[synth][ch{chapter.number:02d}] adaptive budget: "
                f"{_iter_budget} iters (vault has {len(code_vault)} hashes)"
            )
        try:
            for iteration in range(resume_from_iter, _iter_budget):
                # 1. Synthesize — Tier 3 #21 structured output.
                #    Returns a ChapterOutput (sections + challenges + flashcards),
                #    NOT free-form markdown. The LLM can't emit fences in prose_md
                #    (schema discourages; audit enforces); it only lists which
                #    vault hashes go in each section via `code_refs`.
                try:
                    chapter_output = await _synthesize_attempt(
                        chapter = chapter,
                        files_content = files_content,
                        framework = framework,
                        tone_block = tone_block,
                        previous_adjustments = adjustments,
                        llm = llm,
                        iteration = iteration,
                        study_id = payload.get("study_id"),
                    )
                except PydanticValidationError as ve:
                    # 2026-04-24 (Run-8 ch01): LLM produced structured output
                    # that violated a Pydantic constraint (e.g., flashcards
                    # min_length). Treat as a Self-Refine signal instead of
                    # terminal — append targeted feedback and continue so
                    # the next iteration gets a chance to fix it.
                    ve_msg = "; ".join(
                        f"{'.'.join(str(p) for p in err.get('loc', []))}: {err.get('msg', '')}"
                        for err in ve.errors()[:5]
                    )
                    logger.warning(
                        f"[synth][ch{chapter.number:02d}] iter {iteration} "
                        f"Pydantic schema violation — forcing refine: {ve_msg}"
                    )
                    adjustments.append(
                        "SCHEMA VIOLATION in prior output:\n"
                        f"{ve_msg}\n\n"
                        "Your next attempt MUST satisfy every field's "
                        "constraints (min_length, max_length, required fields, "
                        "etc.) — read the schema's field descriptions carefully."
                    )
                    continue
                except Exception as e:
                    raise RuntimeError(
                        f"Synthesizer failed on chapter {chapter.number} iter {iteration}: {e}"
                    ) from e
                # 1b. Integrity audit (Tier 0c, #21 variant + batch-3 2026-04-23).
                #     Union + uniqueness + distribution against the vault:
                #       - `missing` = vault hashes the LLM forgot to reference
                #       - `invented` = hashes in code_refs not present in vault
                #       - `fence_sections` = sections with ``` in prose_md
                #       - `duplicated_refs` = hashes in MORE THAN ONE section
                #         (Run-4 distribution bug: LLM put same hash into 2
                #          sections to satisfy union check; assembler then
                #          rendered the code block twice in unrelated places)
                #       - `empty_sections` = sections with substantive prose
                #         but zero code_refs (distribution failure; see
                #         batch-3 roadmap analysis of Run-4 output)
                #     Any of these = forced refine with targeted feedback.
                (
                    missing,
                    invented,
                    fence_sections,
                    duplicated_refs,
                    empty_sections,
                    thin_sections,
                ) = _audit_structured_output_refs(chapter_output, code_vault)
                n_issues = (
                    len(missing) + len(invented) + len(fence_sections)
                    + len(duplicated_refs) + len(empty_sections)
                    + len(thin_sections)
                )
                # OP-31 (2026-04-25) — thin-section ACCEPT allowance.
                # Run-11 evidence: ch04/ch08/ch10 reached iters with the
                # shape (0/0/0/0/0/N thin) and were perfect on every other
                # dimension — but the gate forced refine and the LLM
                # subsequently regressed (OP-7 had to fire). Thin alone
                # is "could be better prose density", not a structural
                # defect. Allow up to N=3 real-thin sections to ACCEPT
                # provided every other dimension is clean. The chapter-
                # level zero-citation sentinel (__zero_citations__) is
                # special — it ALWAYS forces refine regardless of count.
                _THIN_SECTIONS_ACCEPT_LIMIT = 3
                _real_thin_count = sum(
                    1 for h in thin_sections if h != _ZERO_CITATIONS_MARKER
                )
                _has_zero_citations = _ZERO_CITATIONS_MARKER in thin_sections
                _thin_blocks_accept = (
                    _has_zero_citations
                    or _real_thin_count > _THIN_SECTIONS_ACCEPT_LIMIT
                )
                if (missing or invented or fence_sections or duplicated_refs
                        or empty_sections or _thin_blocks_accept):
                    logger.warning(
                        f"[synth][ch{chapter.number:02d}] iter {iteration} "
                        f"structured-output audit FAILED: "
                        f"{len(missing)} missing / {len(invented)} invented / "
                        f"{len(fence_sections)} fence-contaminated / "
                        f"{len(duplicated_refs)} duplicated / "
                        f"{len(empty_sections)} empty-but-proseful / "
                        f"{len(thin_sections)} thin/zero-citation section(s) "
                        f"out of {len(code_vault)} vault hashes — "
                        f"forcing refine with targeted feedback"
                    )
                    # OP-12: track least-bad audit-failed iter for best-effort
                    # commit if Self-Refine budget exhausts without any graded
                    # iter. Assemble markdown now so a later commit can skip
                    # the assembly step entirely.
                    try:
                        assembled_audit_fail = _assemble_chapter_markdown(
                            chapter_output, code_vault, chapter_title = chapter.title,
                        )
                    except Exception as _ae:
                        assembled_audit_fail = None
                    if assembled_audit_fail is not None and (
                        best_audit_iter is None
                        or n_issues < best_audit_iter["n_issues"]
                    ):
                        best_audit_iter = {
                            "output": chapter_output,
                            "assembled": assembled_audit_fail,
                            "n_issues": n_issues,
                            "iteration": iteration,
                            "missing": missing,
                            "invented": invented,
                            "fence_sections": fence_sections,
                            "duplicated_refs": duplicated_refs,
                            "empty_sections": empty_sections,
                            "thin_sections": thin_sections,
                        }
                    # OP-7: audit-regression early-stop. If this iter is
                    # dramatically worse than the previous iter's audit,
                    # break early — the LLM is drifting (classic Self-Refine
                    # over-correction, Huang 2024 §3.3). Commits whatever
                    # best_audit_iter / best_synthesis we have so far.
                    if (prev_n_issues is not None
                            and prev_n_issues > 0
                            and n_issues > _AUDIT_REGRESSION_FACTOR * prev_n_issues):
                        logger.info(
                            f"[synth][ch{chapter.number:02d}] iter {iteration} "
                            f"audit REGRESSED {prev_n_issues} → {n_issues} "
                            f"issues ({n_issues / max(1, prev_n_issues):.1f}× > "
                            f"{_AUDIT_REGRESSION_FACTOR}× threshold); "
                            f"stopping Self-Refine early"
                        )
                        prev_n_issues = n_issues
                        break
                    prev_n_issues = n_issues
                    # OP-11 (2026-04-25) — refine from best-seen iter, not
                    # last. Run-11 evidence: ch01 best=iter 1 (19 issues),
                    # iter 2/3 worse → iter 3 hit 4× regression and OP-7
                    # killed the loop. ch08 went 26 → 5 → 31 (6.2× regression).
                    # The Self-Refine LLM has no visibility into earlier
                    # iters' quality and keeps drifting from whatever the
                    # immediate-previous iter looked like. Inject a
                    # best-seen anchor message when a prior iter was clearly
                    # better than the current one — text-level reference,
                    # since the LLM regenerates fresh each iter and doesn't
                    # see prior structured output directly.
                    _best_anchor = ""
                    if (best_audit_iter is not None
                            and best_audit_iter["iteration"] < iteration
                            and best_audit_iter["n_issues"] < n_issues):
                        _best_anchor = (
                            f"\n\n**ANCHOR — your iter {best_audit_iter['iteration']} "
                            f"was your best attempt** ({best_audit_iter['n_issues']} "
                            f"audit issue(s) vs this iter's {n_issues}). "
                            f"In that iter you had: "
                            f"{len(best_audit_iter['missing'])} missing, "
                            f"{len(best_audit_iter['invented'])} invented, "
                            f"{len(best_audit_iter['fence_sections'])} fence, "
                            f"{len(best_audit_iter['duplicated_refs'])} duplicated, "
                            f"{len(best_audit_iter['empty_sections'])} empty, "
                            f"{len(best_audit_iter['thin_sections'])} thin. "
                            "Recover to that quality level: keep the structural "
                            "discipline of iter "
                            f"{best_audit_iter['iteration']} and apply ONLY the "
                            "targeted fixes below — do not rewrite from scratch."
                        )
                    # OP-32 (2026-04-25) — hierarchical refine feedback.
                    # Run-11 evidence: ch01 thin sections grew 7 → 12 → 18
                    # monotonically. The LLM was simultaneously told "fix
                    # empty sections" (which redistributes refs across more
                    # sections) AND "fix thin sections" (which needs MORE
                    # prose per ref). Conflicting directions = the LLM
                    # makes both worse. Tier the feedback: only include
                    # thin-section feedback once the structural defects
                    # are mostly fixed (combined < 5). Until then, focus
                    # the refine prompt on the structural issues alone.
                    _structural_defects = (
                        len(missing) + len(invented) + len(fence_sections)
                        + len(duplicated_refs) + len(empty_sections)
                    )
                    _thin_feedback = (
                        thin_sections if _structural_defects < 5 else []
                    )
                    # OP-33 (2026-04-25) — anti-hallucination prompt
                    # hardening. When iter N had invented > 0, iter N+1
                    # MUST get a strict whitelist of valid 12-hex hashes.
                    # Run-11 ch02 iter 1 invented 76 hashes; recovered on
                    # iter 2 with strong feedback but cost a full iter
                    # of compute. Generic "don't invent" wasn't enough;
                    # explicit "ONLY these are valid" is.
                    _hallucination_guard = ""
                    if invented:
                        _valid_bare_hashes = sorted({
                            sentinel[3:15] for sentinel in code_vault
                            if len(sentinel) >= 15
                        })
                        _hallucination_guard = (
                            "\n\n**STRICT WHITELIST (HARD RULE):** the only "
                            "valid 12-hex `code_refs` values for this chapter "
                            f"are exactly these {len(_valid_bare_hashes)} "
                            "hashes. Any value not in this list is a "
                            "hallucinated hash that fails the chapter:\n"
                            + ", ".join(f"`{h}`" for h in _valid_bare_hashes[:50])
                            + (f", ...({len(_valid_bare_hashes) - 50} more)"
                               if len(_valid_bare_hashes) > 50 else "")
                            + "\n\nIf you cannot place a code block, omit it "
                            "from `code_refs` rather than fabricating a hash."
                        )
                    adjustments.append(
                        _format_structured_output_feedback(
                            missing,
                            invented,
                            fence_sections,
                            code_vault,
                            duplicated_refs = duplicated_refs,
                            empty_sections = empty_sections,
                            thin_sections = _thin_feedback,
                        )
                        + _hallucination_guard
                        + _best_anchor
                    )
                    continue
                # Audit passed — clear regression tracker so next iter
                # doesn't compare against a stale value.
                prev_n_issues = 0
                # 1c. Deterministic assembly — build the final chapter markdown
                #     from ChapterOutput + vault. Code fences come from the
                #     vault verbatim; prose comes from the LLM. Downstream
                #     nodes (grader, artifact writer, curator) consume a
                #     ChapterSynthesis-shaped object so their interface is
                #     unchanged.
                assembled_md = _assemble_chapter_markdown(
                    chapter_output, code_vault, chapter_title = chapter.title,
                )
                synthesis = ChapterSynthesis(
                    content = assembled_md,
                    challenges = chapter_output.challenges,
                    flashcards = chapter_output.flashcards,
                )
                # 2. Grade
                # OP-17 (2026-04-25) — pass audit signals to the grader so
                # it can calibrate borderline accept calls (especially
                # citation_integrity + code_preservation_ratio) against
                # deterministic facts instead of re-deriving by inspection.
                _audit_summary_str = (
                    f"hashes_total={len(code_vault)}, "
                    f"missing={len(missing)}, invented={len(invented)}, "
                    f"fence_contaminated={len(fence_sections)}, "
                    f"duplicated={len(duplicated_refs)}, "
                    f"empty_but_proseful={len(empty_sections)}, "
                    f"thin_sections={len(thin_sections)} "
                    f"(real_thin={_real_thin_count}, "
                    f"zero_citations={'yes' if _has_zero_citations else 'no'})"
                )
                try:
                    evaluation = await _grade_attempt(
                        synthesis_text = synthesis.content,
                        chapter = chapter,
                        user_profile = user_profile,
                        framework = framework,
                        llm = llm,
                        iteration = iteration,
                        study_id = payload.get("study_id"),
                        audit_summary = _audit_summary_str,
                    )
                except Exception as e:
                    raise RuntimeError(
                        f"Grader failed on chapter {chapter.number} iter {iteration}: {e}"
                    ) from e
                history.append((synthesis, evaluation))
                logger.info(
                    f"[synth][ch{chapter.number:02d}] iter {iteration} — "
                    f"score={evaluation.weighted_score:.2f} action={evaluation.action} "
                    f"issues={len(evaluation.specific_issues)} preservation=1.00"
                )
                # Track argmax — first graded iteration bootstraps `best_*`.
                if best_eval is None or evaluation.weighted_score > best_eval.weighted_score:
                    best_synthesis = synthesis
                    best_eval = evaluation
                # Tier 3 #13 (2026-04-24) — persist partial progress after every
                # graded iteration so a later-iter cascade timeout / cancel
                # doesn't destroy the `best_*` work. Next run resumes from
                # `iteration + 1` with the same `adjustments` + `best_*`.
                # Best-effort; storage hiccup shouldn't abort synthesis.
                try:
                    await cache.set_chapter_partial(
                        framework = framework,
                        version = version,
                        profile_hash = profile_hash,
                        chapter_num = chapter.number,
                        chapter_title = chapter.title,
                        assigned_files = chapter.assigned_files,
                        iteration_reached = iteration + 1,
                        best_score = best_eval.weighted_score,
                        best_synthesis_md = best_synthesis.content,
                        best_challenges = best_synthesis.challenges,
                        best_flashcards_json = [
                            f.model_dump() for f in best_synthesis.flashcards
                        ],
                        best_evaluation_json = best_eval.model_dump(),
                        adjustments = adjustments,
                        history_scores = [e.weighted_score for _, e in history],
                    )
                except Exception as e:
                    logger.warning(
                        f"[synth][ch{chapter.number:02d}] partial cache persist "
                        f"failed (continuing): {e}"
                    )
                # 3. Decide
                accept_threshold = user_profile.acceptance_threshold
                if evaluation.action == "accept" or evaluation.weighted_score >= accept_threshold:
                    # Accepted — `best_*` already tracks this iteration (argmax)
                    break
                # Early-stop on regression across GRADED iterations. Preservation
                # failures skip history append, so len(history) — not the loop
                # variable — is the right gate for this check.
                if len(history) >= 2:
                    prev_score = history[-2][1].weighted_score
                    if evaluation.weighted_score < prev_score - _REGRESSION_EPSILON:
                        logger.info(
                            f"[synth][ch{chapter.number:02d}] regression detected at iter "
                            f"{iteration} ({evaluation.weighted_score:.2f} < "
                            f"{prev_score:.2f} - {_REGRESSION_EPSILON}); stopping early "
                            f"— committing best seen (score={best_eval.weighted_score:.2f})"
                        )
                        break
                # Structural problem → drop adjustments, start fresh on next iter
                if evaluation.action == "regenerate":
                    adjustments = []
                    continue
                # Localized issues → generate specific adjustment, retry
                if iteration < MAX_SELF_REFINE_ITERATIONS - 1:
                    # Generate adjustment feedback with T=0.7 refiner chain for
                    # exploration (Madaan 2023, Self-Refine §2 — T=0 collapses
                    # exploration and commits to a single deterministic edit path,
                    # a documented cause of regression per Huang 2024 §3.3).
                    # Grader stays T=0; only this critique-generation call benefits
                    # from higher temperature.
                    from services.llm_chain import build_refine_llm_chain
                    refine_llm = build_refine_llm_chain()
                    adj = await _generate_adjustment(evaluation, synthesis.content, refine_llm)
                    adjustments.append(adj)
                    logger.info(
                        f"[synth][ch{chapter.number:02d}] adjustment generated ({len(adj)} chars) "
                        f"— retrying with {len(adjustments)} total adjustment(s)"
                    )
            # 4. Commit BEST — argmax over iterations, not the last.
            # OP-12 (2026-04-24, post-Run-9): if no iter reached the grader
            # (best_synthesis/best_eval still None) BUT at least one iter
            # produced a valid ChapterOutput we could assemble, commit that
            # least-bad audit-failed iter as a best-effort with a DEBT flag.
            # Rationale: Run-9 sentinel'd 6 chapters that each had a viable
            # near-clean audit-failed iter; shipping those as "below-threshold
            # + DEBT" is strictly better than shipping nothing.
            if (best_synthesis is None or best_eval is None) and best_audit_iter is not None:
                logger.warning(
                    f"[synth][ch{chapter.number:02d}] OP-12 RESCUE — no graded "
                    f"iter but audit-failed iter {best_audit_iter['iteration']} "
                    f"has only {best_audit_iter['n_issues']} issue(s) "
                    f"(missing={len(best_audit_iter['missing'])} / "
                    f"invented={len(best_audit_iter['invented'])} / "
                    f"empty={len(best_audit_iter['empty_sections'])}). "
                    f"Committing as best-effort with DEBT flag."
                )
                best_synthesis = ChapterSynthesis(
                    content = best_audit_iter["assembled"],
                    challenges = best_audit_iter["output"].challenges,
                    flashcards = best_audit_iter["output"].flashcards,
                )
                # Synthesize a minimal GraderEvaluation reflecting the audit
                # state so downstream assembler/critic see something coherent.
                # Score is 0.0 (below threshold) → chapter will be re-graded
                # on next run anyway via the sentinel-retry cache path.
                from schemas.knowledge.agents import Issue as _Issue
                best_eval = GraderEvaluation(
                    signal_to_noise = 0.0,
                    assumption_match = 0.0,
                    job_alignment = 0.0,
                    citation_integrity = 0.0,
                    code_density = 0.0,
                    portfolio_synergy = 0.0,
                    complexity_appropriate = 0.0,
                    market_analysis = 0.0,
                    code_preservation_ratio = max(
                        0.0,
                        1.0 - (len(best_audit_iter["missing"]) + len(best_audit_iter["invented"]))
                            / max(1, len(code_vault)),
                    ),
                    weighted_score = 0.0,
                    specific_issues = [_Issue(
                        span_quote = best_audit_iter["assembled"][:200],
                        dimension = "signal_to_noise",
                        suggestion = (
                            f"OP-12 best-effort commit — audit had "
                            f"{best_audit_iter['n_issues']} unresolved issues "
                            f"(missing={len(best_audit_iter['missing'])}, "
                            f"empty={len(best_audit_iter['empty_sections'])}). "
                            f"Regenerate on next run."
                        ),
                    )],
                    action = "refine",
                )
            if best_synthesis is None or best_eval is None:
                raise RuntimeError(
                    f"Chapter {chapter.number} produced no synthesis after "
                    f"{MAX_SELF_REFINE_ITERATIONS} iterations"
                )
            # Tier 3 #13 (2026-04-24) — normal completion reached (whether
            # above- or below-threshold). Clear the partial cache — the
            # next run either hits the full-accept cache (if above) or
            # starts fresh (if below), not resumes mid-refine.
            # NB: on the sentinel path below, we DON'T clear — next run
            # needs to resume to have a shot at finishing.
            try:
                await cache.clear_chapter_partial(
                    framework = framework,
                    version = version,
                    profile_hash = profile_hash,
                    chapter_num = chapter.number,
                )
            except Exception as e:
                logger.warning(
                    f"[synth][ch{chapter.number:02d}] partial cache clear "
                    f"failed (non-fatal): {e}"
                )
        except RuntimeError as terminal_error:
            # OP-19 (2026-04-24, post-Run-10) — before surrendering to the
            # sentinel path, check whether we have a viable best_audit_iter
            # from a PRIOR iteration of the same chapter. Run-10 lost ch07,
            # ch01, ch04 to this exact edge case: early iters produced near-
            # clean audit-failed ChapterOutputs (18-29 issues) but a later
            # iter's synth call hit the 1200s outer timeout, which throws,
            # which bypasses the post-loop OP-12 rescue and drops straight
            # to sentinel. OP-19 duplicates the rescue check here so a synth
            # timeout / cascade exhaustion doesn't discard good earlier work.
            if best_synthesis is None and best_audit_iter is not None:
                logger.warning(
                    f"[synth][ch{chapter.number:02d}] OP-19 RESCUE — synth "
                    f"exception bypassed post-loop; recovering best_audit_iter "
                    f"{best_audit_iter['iteration']} with "
                    f"{best_audit_iter['n_issues']} issue(s) "
                    f"(missing={len(best_audit_iter['missing'])} / "
                    f"invented={len(best_audit_iter['invented'])} / "
                    f"empty={len(best_audit_iter['empty_sections'])}). "
                    f"Committing as best-effort with DEBT flag; synth error was: "
                    f"{str(terminal_error)[:160]}"
                )
                best_synthesis = ChapterSynthesis(
                    content = best_audit_iter["assembled"],
                    challenges = best_audit_iter["output"].challenges,
                    flashcards = best_audit_iter["output"].flashcards,
                )
                from schemas.knowledge.agents import Issue as _Issue
                best_eval = GraderEvaluation(
                    signal_to_noise = 0.0,
                    assumption_match = 0.0,
                    job_alignment = 0.0,
                    citation_integrity = 0.0,
                    code_density = 0.0,
                    portfolio_synergy = 0.0,
                    complexity_appropriate = 0.0,
                    market_analysis = 0.0,
                    code_preservation_ratio = max(
                        0.0,
                        1.0 - (len(best_audit_iter["missing"]) + len(best_audit_iter["invented"]))
                            / max(1, len(code_vault)),
                    ),
                    weighted_score = 0.0,
                    specific_issues = [_Issue(
                        span_quote = best_audit_iter["assembled"][:200],
                        dimension = "signal_to_noise",
                        suggestion = (
                            f"OP-19 best-effort commit after synth exception "
                            f"({best_audit_iter['n_issues']} audit issues in iter "
                            f"{best_audit_iter['iteration']}). "
                            f"Regenerate on next run."
                        ),
                    )],
                    action = "refine",
                )
                # Fall through to the normal post-loop commit path by
                # mutating iteration so the score_trace log uses a sensible
                # number, then RAISING OUT of the except handler into the
                # success branch is not possible — so we inline the commit
                # logic here.
                iteration = best_audit_iter["iteration"]
                # Partial-cache behavior: keep it (next run may still want
                # to refine), same as the normal sentinel path previously did.
                score_trace = f"(audit-rescue at iter {best_audit_iter['iteration']})"
                logger.info(
                    f"[synth][ch{chapter.number:02d}] best score=0.00 via OP-19 "
                    f"(0 graded iters; trajectory: {score_trace})"
                )
                result = await _write_chapter_artifacts(
                    storage, study_root, chapter.number, best_synthesis
                )
                result["score"] = 0.0
                result["iterations"] = 0
                result["debt"] = {
                    "reason": "op19_rescue_audit_failed_but_close",
                    "synth_error": str(terminal_error),
                    "audit_issues": best_audit_iter["n_issues"],
                    "missing": len(best_audit_iter["missing"]),
                    "invented": len(best_audit_iter["invented"]),
                    "empty": len(best_audit_iter["empty_sections"]),
                    "rescued_iter": best_audit_iter["iteration"],
                }
                return {"synthesis_results": [result]}

            # True sentinel path — no best_audit_iter either, truly no output.
            logger.error(
                f"[synth][ch{chapter.number:02d}] TERMINAL FAILURE at iter "
                f"{iteration} after {len(history)} graded iteration(s) — "
                f"{terminal_error}. Emitting DEBT sentinel so sibling "
                f"chapters continue; this chapter will be regenerated on "
                f"the next run."
            )
            sentinel_result = {
                "number": chapter.number,
                "content_path": None,
                "challenges_path": None,
                "flashcards_path": None,
                "score": 0.0,
                "iterations": len(history),
                "debt": {
                    "reason": "synth_chain_exhausted",
                    "error": str(terminal_error),
                    "iteration_failed_at": iteration,
                    "graded_iterations": len(history),
                    "adjustments_accumulated": len(adjustments),
                },
            }
            return {"synthesis_results": [sentinel_result]}
        # Log summary: all iteration scores so operators can see the trajectory.
        score_trace = " → ".join(f"{e.weighted_score:.2f}" for _, e in history)
        logger.info(
            f"[synth][ch{chapter.number:02d}] best score={best_eval.weighted_score:.2f} "
            f"across {len(history)} iterations (trajectory: {score_trace})"
        )
        result = await _write_chapter_artifacts(storage, study_root, chapter.number, best_synthesis)
        result["score"] = best_eval.weighted_score
        result["iterations"] = len(history)
        accepted_above_threshold = (
            best_eval.weighted_score >= user_profile.acceptance_threshold
        )
        if not accepted_above_threshold:
            logger.warning(
                f"[synth][ch{chapter.number:02d}] accepted BELOW threshold "
                f"(score={best_eval.weighted_score:.2f} < {user_profile.acceptance_threshold}) "
                f"after {len(history)} iterations — DEBT flagged for Assembler"
            )
            # Pass debt signal for Assembler to write into DEBT.md.
            # Serialize Issue models to plain dicts so downstream JSON + the
            # operator.add reducer handle them cleanly (Pydantic models can
            # choke msgpack checkpoints in LangGraph state).
            _issues_serialized = [
                i.model_dump() if hasattr(i, "model_dump") else i
                for i in (best_eval.specific_issues or [])
            ]
            result["debt"] = {
                "reason": "score_below_threshold",
                "final_score": best_eval.weighted_score,
                "threshold": user_profile.acceptance_threshold,
                "specific_issues": _issues_serialized,
            }

        # 2) Populate cache when the chapter was accepted above threshold,
        #    OR when OP-26 skip_below_threshold is True and the chapter was
        #    committed below threshold with DEBT. Default (False) preserves
        #    historical behavior — below-threshold chapters stay in
        #    study_root with DEBT flag but are regenerated on the next
        #    same-identity run.
        skip_below_flag = bool(payload.get("skip_below_threshold", False))
        should_cache = accepted_above_threshold or (
            skip_below_flag
            and "content_path" in result
            and not result.get("content_path", "").startswith("SENTINEL:")
        )
        if should_cache:
            try:
                await cache.set_chapter(
                    framework = framework,
                    version = version,
                    profile_hash = profile_hash,
                    chapter_num = chapter.number,
                    chapter_title = chapter.title,
                    assigned_files = chapter.assigned_files,
                    study_root = study_root,
                    score = best_eval.weighted_score,
                    iterations = len(history),
                    best_effort = (not accepted_above_threshold),
                )
            except Exception as e:
                logger.warning(
                    f"[synth][ch{chapter.number:02d}] cache write failed "
                    f"(continuing): {e}"
                )

        return {
            "synthesis_results": [result],
        }

    # =========================================================================
    # Node: Curator — single-model style normalization pass
    # =========================================================================
    async def curator(
        self,
        state: KnowledgeDistillerState,
        curator_llm: ChatOpenAI,
        storage: MinIOStudyStorage) -> dict:
        """
        Style-normalize every accepted chapter using ONE pinned model.

        Context: synthesize_chapter's fallback chain may land different
        chapters on different models under load → inconsistent voice/tone/
        heading conventions across a single study. The curator runs ONE
        model over each chapter sequentially, rewriting ONLY for style
        consistency. Facts, citations, and code blocks are preserved.

        Research basis (agent-researched, 2026-04-20):
          - Mixture-of-Agents (arXiv 2406.04692): an aggregator model over
            heterogeneous proposers reliably improves final quality.
          - HMS Analytical Software multi-agent doc pattern: a "Holistic
            Agent" at the end smooths style drift between sections written
            in isolation.

        IMPORTANT: curator writes to the SAME MinIO key as synthesize_chapter
        (chapterNN/README.md), so the critic (which runs AFTER) judges the
        curated text, not the drafts.
        """
        study_root = state["study_root"]
        framework = state["framework"]
        user_profile = state["user_profile"]
        plan = state["plan"]

        # Load all current chapter READMEs (the output of synthesize_chapter)
        chapters = await _load_all_chapters(storage, study_root, plan)
        if not chapters:
            logger.warning("[curator] no chapters found — skipping curator pass")
            return {"current_phase": "critic"}

        glossary = _extract_glossary_terms(chapters, max_terms = 12)
        glossary_str = (
            "\n".join(f"  - {t}" for t in glossary) if glossary else "  (none extracted)"
        )
        tone_block = build_tone_block(user_profile)

        chain = CURATOR_PROMPT | curator_llm

        # Tier 2 #8 (2026-04-24) — parallel curator over chapters.
        # Was sequential (~10 min for 9 chapters on GLM-5.1); with
        # Semaphore(2) = 2 concurrent chapters we halve wall-clock
        # without overloading rate limits (Router cascades on 429 anyway).
        # Same single pinned-style model is still used per chapter, just
        # overlapped — style consistency is per-chapter, not per-study.
        _CURATOR_SEMAPHORE = asyncio.Semaphore(2)

        async def _curate_one(n: int, content: str) -> bool:
            """Curate one chapter. Returns True on successful write-back."""
            async with _CURATOR_SEMAPHORE:
                # Tier 0a: vault code blocks before the curator LLM call so the
                # curator can rewrite prose freely while code is physically
                # unreachable by its token generator. Tier 0c: any integrity
                # failure on the curator output → keep original (curator is
                # best-effort style normalization, not a content gate).
                try:
                    vaulted_content, code_vault = _vault_code_blocks(content)
                except ValueError as e:
                    logger.warning(
                        f"[curator][ch{n:02d}] vault collision ({e}); "
                        f"keeping original"
                    )
                    return False
                try:
                    resp = await chain.ainvoke({
                        "chapter_number": n,
                        "framework": framework,
                        "tone_block": tone_block,
                        "glossary": glossary_str,
                        "chapter_content": vaulted_content,
                    })
                except Exception as e:
                    logger.warning(
                        f"[curator][ch{n:02d}] curation failed ({e}); keeping original"
                    )
                    return False
                # OP-21 (2026-04-24, post-Run-10) — normalize LLM response
                # content to a plain string. Some providers (notably Mistral
                # with reasoning tokens, and Claude-style content-block
                # responses) return `resp.content` as a list of blocks:
                #   [{"type": "text", "text": "..."}, {"type": "thinking", ...}]
                # The downstream regex `_audit_sentinel_roundtrip` needs a
                # string. Without this flattener, Run-10 crashed on ch09's
                # curator pass with `TypeError: expected string or bytes-like
                # object, got 'list'`. Safe fallback: coerce anything that
                # isn't already a string to text.
                raw_content = resp.content if hasattr(resp, "content") else resp
                if isinstance(raw_content, list):
                    parts: list[str] = []
                    for block in raw_content:
                        if isinstance(block, str):
                            parts.append(block)
                        elif isinstance(block, dict):
                            # Most SDKs emit {"type": "text", "text": "..."}
                            # Keep only text-like blocks; drop "thinking" /
                            # "tool_use" blocks that don't belong in prose.
                            btype = block.get("type", "")
                            if btype in ("text", "output_text") or btype == "":
                                parts.append(str(block.get("text", "")))
                        else:
                            parts.append(str(block))
                    curated_vaulted = "\n".join(p for p in parts if p)
                elif isinstance(raw_content, str):
                    curated_vaulted = raw_content
                else:
                    curated_vaulted = str(raw_content)
                missing, unexpected = _audit_sentinel_roundtrip(
                    curated_vaulted, code_vault
                )
                if missing or unexpected:
                    logger.warning(
                        f"[curator][ch{n:02d}] preservation FAILED "
                        f"({len(missing)} missing, {len(unexpected)} unexpected "
                        f"of {len(code_vault)} vaulted); keeping original"
                    )
                    return False
                curated = _restore_code_blocks(curated_vaulted, code_vault)
                if len(curated.strip()) < 0.5 * len(content.strip()):
                    logger.warning(
                        f"[curator][ch{n:02d}] output shrank drastically "
                        f"({len(curated)} < 0.5×{len(content)}); keeping original"
                    )
                    return False
                await storage.write(
                    f"{study_root}/chapter{n:02d}/README.md",
                    curated.strip() + "\n",
                    content_type = "text/markdown",
                )
                logger.info(
                    f"[curator][ch{n:02d}] normalized "
                    f"({len(content)}B → {len(curated)}B, "
                    f"{len(code_vault)} code blocks preserved)"
                )
                return True

        # `_load_all_chapters` returns `(number, title, body)` — 3-tuples.
        # Ignore title (used only by the critic's deterministic coverage scan).
        results = await asyncio.gather(
            *(_curate_one(n, body) for n, _title, body in chapters),
            return_exceptions = False,
        )
        curated_count = sum(1 for r in results if r)
        logger.info(
            f"[curator] pass complete — {curated_count}/{len(chapters)} "
            f"chapters rewritten for style consistency"
        )
        return {"current_phase": "critic"}

    # =========================================================================
    # Node: Critic
    # =========================================================================
    async def critic(
        self,
        state: KnowledgeDistillerState,
        llm: ChatOpenAI,
        storage: MinIOStudyStorage) -> dict:
        """
        RAGAS-style post-synthesis verification. Runs ONCE after all chapters
        have been accepted by the Self-Refine loop.

        HYBRID MEASUREMENT:
          - citation_coverage: DETERMINISTIC — regex scan chapter READMEs for
            '# docs: <slug>' and intersect with research/raw/*.md. We don't ask
            the LLM to verify this because we can compute it exactly.
          - faithfulness + code_syntax_valid: LLM — the LLM reads (capped) chapter
            bundles and produces scores + additional issues.
          - overall_score: weighted average of the three, recomputed after we
            override citation_coverage.

        Side effect: writes <study_root>/research/validation_report.json with the
        final CriticAssessment (JSON-dumped).

        Returns a partial state update:
            {"validation_report": dict, "current_phase": "assemble"}

        Raises:
            FileNotFoundError: no chapter READMEs exist (upstream synth didn't run).
            RuntimeError: LLM call failed (Celery retries or marks job failed).
        """
        study_root = state["study_root"]
        framework = state["framework"]
        plan = state["plan"]

        # 1) Load all chapters + the set of available source slugs
        chapters = await _load_all_chapters(storage, study_root, plan)
        if not chapters:
            # Fragility fix (2026-04-23, post-Run-6): all-chapters-sentinel
            # case. 0d-6 emits DEBT sentinels with content_path=None for
            # chapters that exhausted the fallback chain. If EVERY chapter
            # sentinel'd (mass provider outage, catalog drift, etc.), there
            # are no READMEs to critique. Do NOT crash — emit a minimal
            # CriticAssessment so assembler can still write DEBT.md +
            # summary.md describing the wipeout.
            synth_results = state.get("synthesis_results") or []
            sentinel_count = sum(
                1 for r in synth_results
                if (r.get("debt") or {}).get("reason") == "synth_chain_exhausted"
            )
            logger.warning(
                f"[critic] no chapter READMEs under {study_root!r} — "
                f"all {len(plan)} chapter(s) sentinel'd "
                f"({sentinel_count} via synth_chain_exhausted). Producing "
                f"zero-score CriticAssessment; assembler will render DEBT.md."
            )
            wipeout_msg = (
                f"WIPEOUT: 0/{len(plan)} chapters produced output; "
                f"{sentinel_count} sentinel'd on synth-chain exhaustion. "
                "See DEBT.md for per-chapter cause. Likely provider outage, "
                "rate-limit exhaustion, or catalog drift (e.g., model newly "
                "blocked at org/project level)."
            )
            final = CriticAssessment(
                citation_coverage = 0.0,
                faithfulness = 0.0,
                code_syntax_valid = 0.0,
                overall_score = 0.0,
                issues = [wipeout_msg],
            )
            report_key = f"{study_root}/research/validation_report.json"
            await storage.write(
                report_key,
                final.model_dump_json(indent = 2),
                content_type = "application/json",
            )
            return {
                "validation_report": final.model_dump(),
                "current_phase": "assemble",
            }
        available_slugs = await _load_available_slugs(storage, study_root)

        # 2) Deterministic citation scan
        cited, citation_issues = _scan_citations(chapters, available_slugs)
        if cited:
            citation_coverage = sum(1 for s in cited if s in available_slugs) / len(cited)
        else:
            citation_coverage = 0.0  # no citations at all = synthesizer skipped them
            citation_issues.append(
                "no '# docs:' citations found in any chapter — synthesizer may be ignoring the citation requirement"
            )
        logger.info(
            f"[critic] citations: {len(cited)} cited, {len(citation_issues)} broken, "
            f"coverage={citation_coverage:.2f}"
        )

        # 3) LLM assessment (faithfulness + code_syntax_valid + issues)
        #
        # OP-30 (2026-04-25, post-Run-11): wrap the LLM critic call in
        # try/except. Run-11 had 9 chapters successfully written to MinIO
        # then this call raised RuntimeError (LiteLLM Router returned None
        # — all healthy models emitted non-parseable output). The exception
        # bubbled up through critic → assembler never ran → Celery task
        # failed → no summary.md, no DEBT.md. Deterministic fallback:
        # treat faithfulness + code_syntax_valid as 0.0 and add an issue
        # flagging the LLM-critic outage. The chapter content is still on
        # disk; assembler will still produce summary + DEBT from the
        # deterministic signals (citation_coverage, linter, fence_scan).
        bundles = _build_chapter_bundles(chapters)
        try:
            llm_assessment: CriticAssessment = await _invoke_structured_with_fallback(
                prompt = CRITIC_PROMPT,
                llm = llm,
                schema = CriticAssessment,
                invoke_vars = {
                    "framework": framework,
                    "file_slugs": ", ".join(sorted(available_slugs)),
                    "chapter_bundles": bundles,
                },
                label = "critic",
            )
        except Exception as e:
            # OP-35 (2026-04-25) — per-chapter fallback BEFORE the
            # deterministic-only OP-30 fallback. Run-11 evidence: the
            # 50KB 5-chapter bundle was too large/complex for any model
            # in the cascade to emit parseable structured output. Per-
            # chapter prompts are 5-10× smaller and far less likely to
            # blow the cascade. Try each chapter in isolation; skip
            # individual failures; aggregate what comes back.
            logger.warning(
                f"[critic] bundle LLM assessment failed ({type(e).__name__}: "
                f"{str(e)[:120]}) — attempting OP-35 per-chapter fallback"
            )
            per_ch_faith: list[float] = []
            per_ch_code: list[float] = []
            per_ch_issues: list[str] = []
            per_ch_ok = 0
            for ch_num, ch_title, ch_body in chapters:
                try:
                    snippet_body = ch_body[:8000]  # tight per-chapter cap
                    one_bundle = (
                        f"=== Chapter {ch_num:02d} — {ch_title} ===\n"
                        f"{snippet_body}\n"
                    )
                    one_assess = await _invoke_structured_with_fallback(
                        prompt = CRITIC_PROMPT,
                        llm = llm,
                        schema = CriticAssessment,
                        invoke_vars = {
                            "framework": framework,
                            "file_slugs": ", ".join(sorted(available_slugs)),
                            "chapter_bundles": one_bundle,
                        },
                        label = f"critic-ch{ch_num:02d}",
                    )
                    per_ch_faith.append(float(one_assess.faithfulness))
                    per_ch_code.append(float(one_assess.code_syntax_valid))
                    per_ch_issues.extend(
                        f"[ch{ch_num:02d}] {iss}" for iss in one_assess.issues
                    )
                    per_ch_ok += 1
                except Exception as e_one:
                    logger.warning(
                        f"[critic-ch{ch_num:02d}] OP-35 per-chapter fallback "
                        f"also failed ({type(e_one).__name__}); skipping"
                    )
                    per_ch_issues.append(
                        f"[ch{ch_num:02d}] critic LLM unavailable for this "
                        f"chapter; deterministic checks still applied"
                    )
            if per_ch_ok > 0:
                llm_assessment = CriticAssessment(
                    citation_coverage = citation_coverage,
                    faithfulness = sum(per_ch_faith) / len(per_ch_faith),
                    code_syntax_valid = sum(per_ch_code) / len(per_ch_code),
                    overall_score = 0.0,  # recomputed below
                    issues = per_ch_issues,
                )
                logger.info(
                    f"[critic] OP-35 per-chapter fallback succeeded on "
                    f"{per_ch_ok}/{len(chapters)} chapters: "
                    f"faith={llm_assessment.faithfulness:.2f} "
                    f"code_syntax={llm_assessment.code_syntax_valid:.2f}"
                )
            else:
                # OP-30 — deterministic-only fallback when even per-chapter
                # calls cannot reach a working model. Chapter content on
                # disk is still intact; only the LLM-side judgment is lost.
                logger.warning(
                    f"[critic] OP-30 FALLBACK — no per-chapter LLM calls "
                    f"succeeded either. Using deterministic-only assessment."
                )
                llm_assessment = CriticAssessment(
                    citation_coverage = citation_coverage,
                    faithfulness = 0.0,
                    code_syntax_valid = 0.0,
                    overall_score = 0.0,
                    issues = [
                        f"OP-30 RESCUE: every LLM critic call (bundle + "
                        f"per-chapter) failed. Citation coverage and "
                        f"deterministic linter ran successfully; LLM-only "
                        f"faithfulness + code_syntax_valid scored 0.0. "
                        f"Original bundle error: {type(e).__name__}: "
                        f"{str(e)[:160]}"
                    ],
                )

        # 4) Deterministic style linter — cheap, LLM-free, catches what the
        #    LLM critic is bad at (heading-depth drift, code-density spread,
        #    stub chapters). Runs over the same chapters the LLM just judged.
        linter_issues = _deterministic_linter(chapters)
        if linter_issues:
            logger.info(f"[critic] linter: {len(linter_issues)} style issues flagged")

        # 4b) Tier 2 #20 (2026-04-23) — hallucinated-fence provenance check.
        #     Every code block in every chapter README must match a source
        #     fence (by sha256[:12] hash). Catches any late-drift through
        #     curator or any future node that touches assembled content.
        fence_issues = await _scan_hallucinated_fences(storage, study_root, chapters)
        if fence_issues:
            logger.warning(
                f"[critic] fence-scan: {len(fence_issues)} hallucinated code "
                f"block(s) detected across chapters — flagged in DEBT"
            )

        # 5) Merge — override citation_coverage with our deterministic value; recompute overall.
        merged_issues = list(llm_assessment.issues) + citation_issues + linter_issues + fence_issues
        overall = (
            citation_coverage
            + llm_assessment.faithfulness
            + llm_assessment.code_syntax_valid
        ) / 3.0
        final = CriticAssessment(
            citation_coverage = citation_coverage,
            faithfulness = llm_assessment.faithfulness,
            code_syntax_valid = llm_assessment.code_syntax_valid,
            overall_score = overall,
            issues = merged_issues,
        )
        logger.info(
            f"[critic] final — overall={final.overall_score:.2f} "
            f"faithfulness={final.faithfulness:.2f} "
            f"code_syntax_valid={final.code_syntax_valid:.2f} "
            f"issues={len(final.issues)}"
        )

        # 5) Persist validation_report.json to MinIO
        report_key = f"{study_root}/research/validation_report.json"
        await storage.write(
            report_key,
            final.model_dump_json(indent = 2),
            content_type = "application/json",
        )
        logger.info(f"[critic] wrote {report_key}")

        return {
            "validation_report": final.model_dump(),
            "current_phase": "assemble",
        }

    # =========================================================================
    # Node: Assembler
    # =========================================================================
    async def assembler(
        self,
        state: KnowledgeDistillerState,
        llm: ChatOpenAI,
        storage: MinIOStudyStorage) -> dict:
        """
        Final stage. Produces:
          - summary.md   — LLM-generated reading plan + market roadmap + money
                           projects, via ASSEMBLER_PROMPT. Freeform markdown;
                           no structured output.
          - DEBT.md      — deterministic aggregation of unresolved issues from
                           grader debts (synthesize result['debt']), critic issues
                           (validation_report['issues']), and missing chapters
                           (plan vs synthesis_results delta). No LLM.
          - Episodic memory logging (v1 stub) — logs what we'd persist keyed by
                           (user_id, framework). PG write lands in a future step.

        Returns a partial state update:
            {"summary_path": str, "debt_path": str, "current_phase": "complete"}

        Raises:
            RuntimeError: ASSEMBLER_PROMPT LLM call failed (Celery retries).
        """
        study_root = state["study_root"]
        framework = state["framework"]
        plan = state["plan"]
        synthesis_results = state["synthesis_results"]
        validation_report = state.get("validation_report")
        user_profile = state["user_profile"]
        user_id = state.get("user_id", "default")

        # 1) Load chapter previews for the summary prompt
        previews = await _load_chapter_previews(storage, study_root, plan)
        chapter_summaries = _build_chapter_summaries(previews)
        logger.info(f"[assembler] loaded {len(previews)} chapter previews")

        # Fragility fix (2026-04-23, post-Run-6): all-chapters-sentinel case.
        # If NO chapter produced a README (synthesis wipeout), skip the LLM
        # summary call — a freeform "here's your study" narrative over empty
        # content would be misleading/hallucinatory. Emit a plain-text
        # wipeout notice instead; DEBT.md still renders per-chapter causes.
        sentinel_count = sum(
            1 for r in synthesis_results
            if (r.get("debt") or {}).get("reason") == "synth_chain_exhausted"
        )
        chapters_with_content = sum(1 for r in synthesis_results if r.get("content_path"))
        if chapters_with_content == 0 and synthesis_results:
            logger.warning(
                f"[assembler] WIPEOUT — 0/{len(plan)} chapters produced "
                f"output ({sentinel_count} sentinel'd). Skipping summary "
                f"LLM call; writing plain-text wipeout notice."
            )
            summary_md = (
                f"# {framework} — Study Wipeout\n\n"
                f"This study produced **0 of {len(plan)}** planned chapters.\n\n"
                f"**{sentinel_count}** chapter(s) exhausted the LLM fallback "
                "chain during synthesis (provider outage, rate-limit storm, "
                "or catalog drift — see `DEBT.md` for the specific cause "
                "per chapter).\n\n"
                "Re-running this study with the same `framework`, `version`, "
                "and `user_profile` will reuse cached ingestion + planning "
                "and re-attempt synthesis for the failed chapters "
                "(Tier 3 #13 per-chapter artifact cache recovers any "
                "chapters that succeed between runs).\n"
            )
        else:
            # 2) Generate summary.md via ASSEMBLER_PROMPT (freeform markdown)
            try:
                summary_md = await _call_assembler_llm(
                    framework = framework,
                    user_profile_summary_str = _user_profile_summary(user_profile),
                    chapter_summaries = chapter_summaries,
                    llm = llm,
                )
            except Exception as e:
                raise RuntimeError(f"Assembler LLM call failed: {e}") from e

        summary_key = f"{study_root}/summary.md"
        await storage.write(summary_key, summary_md, content_type = "text/markdown")
        logger.info(f"[assembler] wrote {summary_key} ({len(summary_md)} chars)")

        # 3) Build DEBT.md deterministically — no LLM call
        debt_md = _build_debt_md(plan, synthesis_results, validation_report)
        debt_key = f"{study_root}/DEBT.md"
        await storage.write(debt_key, debt_md, content_type = "text/markdown")
        logger.info(f"[assembler] wrote {debt_key} ({len(debt_md)} chars)")

        # 4) Episodic memory — v1 stub: log what we'd persist
        _log_episodic_memory(
            user_id = user_id,
            framework = framework,
            synthesis_results = synthesis_results,
            validation_report = validation_report,
        )

        return {
            "summary_path": summary_key,
            "debt_path": debt_key,
            "current_phase": "complete",
        }

    # =========================================================================
    # Node: canary_synth — 1-chapter smoke test before Send() fan-out
    # =========================================================================
    async def canary_synth(
        self,
        state: KnowledgeDistillerState,
        synth_llm: ChatOpenAI,
        storage: MinIOStudyStorage,
        cache) -> dict:
        """
        OP-14 (2026-04-24). Safety net: synthesize the SMALLEST chapter first
        (fewest assigned_files). If the call raises, LangGraph bubbles the
        exception up and the whole study fails with a clear error at cost
        of ~1 chapter of compute instead of 8-12.

        Would have caught Run-9's 3-tuple unpack bug and Run-10's OP-21
        list-coerce bug before wasting 8-11 chapters.

        On success: returns the synthesis result AND sets
        `canary_chapter_number` so `fan_out_chapters` skips that chapter.

        On full-cache hit (chapter already synthesized in a prior run):
        synthesize_chapter short-circuits trivially — near-zero cost.
        """
        plan: list = state["plan"]
        if not plan:
            return {"canary_chapter_number": None}
        # Smallest chapter = fewest assigned_files (proxy for vault hash count
        # and prompt size). Ties broken by lower chapter number.
        canary_ch = min(plan, key = lambda c: (len(c.assigned_files), c.number))
        logger.info(
            f"[canary] running smoke test on smallest chapter: "
            f"ch{canary_ch.number:02d} '{canary_ch.title}' "
            f"({len(canary_ch.assigned_files)} files)"
        )
        user_profile = state["user_profile"]
        profile_dict = (
            user_profile.model_dump()
            if hasattr(user_profile, "model_dump")
            else dict(user_profile)
        )
        profile_hash = canonical_profile_hash(profile_dict)
        version = state.get("version") or "latest"
        payload = {
            "chapter": canary_ch,
            "framework": state["framework"],
            "version": version,
            "profile_hash": profile_hash,
            "user_profile": user_profile,
            "study_root": state["study_root"],
            # OP-26 — honor the same cache-write flag as the fan-out workers.
            "skip_below_threshold": state.get("skip_below_threshold", False),
        }
        # Call synthesize_chapter directly — no semaphore, no concurrency.
        # Any exception raised here propagates out of the graph and fails
        # the whole Celery task with a clean traceback pointing at the bug.
        result_dict = await self.synthesize_chapter(
            payload, synth_llm, storage, cache,
        )
        # synthesize_chapter returns {"synthesis_results": [result]} — we
        # forward that verbatim (operator.add reducer accumulates it) and
        # tag the chapter number so fan-out skips it.
        synth_results = result_dict.get("synthesis_results", [])
        logger.info(
            f"[canary] ch{canary_ch.number:02d} smoke test passed — "
            f"proceeding to Send() fan-out on remaining "
            f"{len(plan) - 1} chapter(s)"
        )
        return {
            "synthesis_results": synth_results,
            "canary_chapter_number": canary_ch.number,
        }

    # =========================================================================
    # Conditional Edges
    # =========================================================================
    def fan_out_chapters(
        self,
        state: KnowledgeDistillerState) -> list[Send] | str:
        """
        Conditional-edge function. After canary_synth produces
        state['canary_chapter_number'], emit one Send() per remaining
        chapter so synthesize_chapter runs in parallel for N-1 chapters.

        Each Send carries a MINIMAL payload — just what one worker needs.
        Workers share nothing except what they return via the `operator.add`
        reducer on state['synthesis_results'].

        `profile_hash` is computed ONCE here and threaded into every worker
        so the synthesis cache's keys stay consistent across all N chapters.

        OP-14 (2026-04-24): skip the chapter already synthesized by the
        canary node (state['canary_chapter_number']) to avoid double work.
        If only 1 chapter exists in the plan (canary already handled it),
        return "curator" directly — LangGraph routes to that node instead
        of an empty Send() list (which would stall the graph).
        """
        user_profile = state["user_profile"]
        profile_dict = (
            user_profile.model_dump()
            if hasattr(user_profile, "model_dump")
            else dict(user_profile)
        )
        profile_hash = canonical_profile_hash(profile_dict)
        version = state.get("version") or "latest"
        canary_num = state.get("canary_chapter_number")
        remaining = [ch for ch in state["plan"] if ch.number != canary_num]
        if not remaining:
            return "curator"
        return [
            Send(
                "synthesize_chapter",
                {
                    "chapter": ch,
                    "framework": state["framework"],
                    "version": version,
                    "profile_hash": profile_hash,
                    "user_profile": user_profile,
                    "study_root": state["study_root"],
                },
            )
            for ch in remaining
        ]

    # =========================================================================
    # Build the Knowledge Distiller Graph
    # =========================================================================
    def build_knowledge_distiller_graph(
        self,
        llm: ChatOpenAI,
        storage: MinIOStudyStorage,
        cache: StudyCache,
        synth_llm: ChatOpenAI = None,
        curator_llm: ChatOpenAI = None,
        checkpointer = None,
        max_concurrent_chapters: int = 2,  # Tier 1 #4b 2026-04-23: was 5; restored to match module header docstring after Run-4 stampede observation (5 concurrent NIM reasoning calls → 504 cascade on glm-5.1). K=2 fits NIM free-tier 40 RPM/model comfortably.
        skip_below_threshold: bool = False):  # OP-26 (2026-04-24 late): closure-captured flag — when True, below-threshold best-effort chapters also write to the full cache so subsequent runs skip them.
        """
        Compose the 5 KD nodes into a LangGraph StateGraph.

        Pipeline shape:
            START → ingest → planner → (Send fan-out to N workers, capped by
                  the per-study semaphore) → synthesize_chapter [×N parallel]
                  → (results merged via operator.add) → critic → assembler → END

        Cache: ingest/planner/synthesize_chapter all check the cache and
        short-circuit on hits. Cache writes happen after successful nodes
        (planner on any plan; synthesize only on above-threshold accept).

        Model consistency:
            `max_concurrent_chapters` (default 2) controls how many
            synthesize_chapter workers run in parallel. With 2 concurrent
            workers and NIM's 40 RPM free tier, the PRIMARY model
            realistically serves every chapter's initial call, giving a
            consistent voice across the whole study. Lower = more
            consistency + slower; higher = more parallel throughput +
            more fallback diversity. Set to 1 for strict serialization.

        Dependency injection:
            LangGraph nodes receive only `state` (or a Send `payload`). We wrap
            each method in an async closure that binds `llm` + `storage` +
            `cache` + semaphore at build-time.

        Args:
            llm: fallback chain (app.state.llm).
            storage: shared MinIOStudyStorage instance.
            cache: shared StudyCache instance (wraps `storage`).
            checkpointer: optional AsyncPostgresSaver for run-resumability.
            max_concurrent_chapters: max parallel synthesize_chapter
                calls. Values 1-3 recommended.
        """
        workflow = StateGraph(KnowledgeDistillerState)

        # Per-study semaphore: lives inside this closure so every Celery task
        # gets its own, but all N fan-out workers within a single task share it.
        # Without this, Send() can fire all N synthesize_chapter workers
        # concurrently and pound the primary model into rate-limit → fallbacks
        # → tone divergence across chapters.
        synth_semaphore = asyncio.Semaphore(max_concurrent_chapters)

        # --------------------------------------------------------------------
        # Wrapper closures — bind (llm, storage, cache) into node signatures.
        # LangGraph inspects whether a node function is async via
        # asyncio.iscoroutinefunction(). Local async closures preserve the
        # async signature while closing over dependencies.
        # --------------------------------------------------------------------
        # synth_llm and curator_llm default to the main chain when omitted —
        # this keeps the old call signature working, but production callers
        # should pass synth_llm=build_synth_fallback_chain() (excludes Groq
        # tail for quality) and curator_llm=build_curator_llm() (pinned).
        effective_synth = synth_llm or llm
        effective_curator = curator_llm or llm

        async def _ingest(state):
            return await self.ingest(state, storage, cache)

        async def _planner(state):
            return await self.planner(state, llm, storage, cache)

        async def _synthesize_chapter(payload):
            # Acquire the semaphore before kicking off any LLM calls.
            # This keeps at most `max_concurrent_chapters` chapter syntheses
            # in flight — so the primary model serves them consistently and
            # the fallback chain fires only when the primary is truly down.
            #
            # OP-26: inject the graph-level skip_below_threshold flag into
            # the per-worker payload so synthesize_chapter decides whether
            # to cache below-threshold best-effort outputs for next run.
            payload = {**payload, "skip_below_threshold": skip_below_threshold}
            async with synth_semaphore:
                return await self.synthesize_chapter(payload, effective_synth, storage, cache)

        async def _canary_synth(state):
            # OP-14: safety-net node that synthesizes the smallest chapter
            # before the full Send() fan-out. Any exception here aborts the
            # whole study cheaply instead of after 8-12 wasted chapters.
            # OP-26: propagate skip_below_threshold via state so the canary's
            # own synth can cache below-threshold outputs when flagged.
            return await self.canary_synth(
                {**state, "skip_below_threshold": skip_below_threshold},
                effective_synth, storage, cache,
            )

        async def _curator(state):
            return await self.curator(state, effective_curator, storage)

        async def _critic(state):
            return await self.critic(state, llm, storage)

        async def _assembler(state):
            return await self.assembler(state, llm, storage)

        # --------------------------------------------------------------------
        # Register nodes
        # --------------------------------------------------------------------
        workflow.add_node("ingest", _ingest)
        workflow.add_node("planner", _planner)
        workflow.add_node("canary_synth", _canary_synth)
        workflow.add_node("synthesize_chapter", _synthesize_chapter)
        workflow.add_node("curator", _curator)
        workflow.add_node("critic", _critic)
        workflow.add_node("assembler", _assembler)

        # --------------------------------------------------------------------
        # Entry point + linear edges
        # Pipeline: ingest → planner → canary_synth (OP-14 safety net,
        #           smallest chapter only) → [fan-out synthesize_chapter ×(N-1)]
        #           → curator → critic → assembler → END
        # Curator runs BEFORE critic so the critic judges the final
        # (style-normalized) text, not the raw drafts — otherwise the
        # curator's post-critic rewrites could silently drift facts.
        #
        # OP-14: if canary_synth raises, LangGraph fails the run fast with
        # a clear error and zero wasted fan-out compute.
        # --------------------------------------------------------------------
        workflow.set_entry_point("ingest")
        workflow.add_edge("ingest", "planner")
        workflow.add_edge("planner", "canary_synth")

        # Dynamic fan-out: canary_synth → (N-1) synthesize_chapter workers
        # via Send(). canary's own chapter number is skipped in
        # fan_out_chapters via state['canary_chapter_number'].
        workflow.add_conditional_edges(
            "canary_synth",
            self.fan_out_chapters,
            ["synthesize_chapter", "curator"],  # canary-only plan (1 chapter) skips fan-out
        )

        # Merge: LangGraph waits for ALL N workers (operator.add reducer accumulates
        # synthesis_results) before firing this edge. Fan-in is automatic.
        workflow.add_edge("synthesize_chapter", "curator")
        workflow.add_edge("curator", "critic")
        workflow.add_edge("critic", "assembler")
        workflow.add_edge("assembler", END)

        return workflow.compile(checkpointer = checkpointer)
