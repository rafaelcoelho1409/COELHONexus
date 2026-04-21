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

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from schemas.knowledge.agents import (
    ChapterPlan,
    ChapterPlanList,
    ChapterSynthesis,
    CriticAssessment,
    GraderEvaluation,
    ShardLabels,
)
from schemas.knowledge.ingestion import DocsIngestionConfig
from schemas.knowledge.inputs import UserProfile
from schemas.knowledge.prompts import (
    CHAPTER_REDUCE_PROMPT,
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
    _maybe_split_monolith,
    _read_raw_prefix,
    _validate_plan,
    _write_plan_json,
    # Step 6
    _generate_adjustment,
    _grade_attempt,
    _load_chapter_files,
    _synthesize_attempt,
    _user_profile_summary,
    _write_chapter_artifacts,
    # Step 7 + deterministic linter + glossary (new)
    _build_chapter_bundles,
    _deterministic_linter,
    _extract_glossary_terms,
    _load_all_chapters,
    _load_available_slugs,
    _scan_citations,
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
# retries. 3 total attempts per chapter (initial + 2 refinements). Matches the
# bound recommended by Madaan et al. in the Self-Refine paper.
MAX_SELF_REFINE_ITERATIONS = 5


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
        _shard_start = _asyncio.get_event_loop().time()
        shard_results: list[ShardLabels] = await _asyncio.gather(
            *(_label_shard(s, i + 1) for i, s in enumerate(shards)),
        )
        _shard_elapsed = _asyncio.get_event_loop().time() - _shard_start
        total_clusters = sum(len(r.clusters) for r in shard_results)
        total_shard_unused = sum(len(r.unused_shard_slugs) for r in shard_results)
        logger.info(
            f"[planner] MAP complete: {len(shards)} shards → {total_clusters} "
            f"micro-clusters + {total_shard_unused} shard-unused slugs "
            f"in {_shard_elapsed:.1f}s"
        )

        # ------ REDUCE pass — merge into final ChapterPlanList ---------------
        cluster_lines: list[str] = []
        shard_unused_all: list[str] = []
        for idx, sr in enumerate(shard_results, 1):
            for c in sr.clusters:
                cluster_lines.append(
                    f"- [shard {idx}] {c.cluster_name}: {c.description} "
                    f"({len(c.file_slugs)} files: {', '.join(c.file_slugs[:3])}"
                    f"{'...' if len(c.file_slugs) > 3 else ''})"
                )
            shard_unused_all.extend(sr.unused_shard_slugs)
        cluster_summary_text = "\n".join(cluster_lines)
        all_slugs_text = "\n".join(slug for slug, _ in entries)
        shard_unused_text = (
            "\n".join(f"- {s}" for s in shard_unused_all)
            if shard_unused_all else "(none — reducer may still add more)"
        )
        reduce_chain = CHAPTER_REDUCE_PROMPT | llm.with_structured_output(
            ChapterPlanList, method = "function_calling", include_raw = True,
        )
        try:
            raw_and_parsed: dict = await reduce_chain.ainvoke({
                "framework": framework,
                "shard_count": len(shards),
                "cluster_summary": cluster_summary_text,
                "shard_unused": shard_unused_text,
                "all_slugs": all_slugs_text,
            })
        except Exception as e:
            raise RuntimeError(f"Planner REDUCE call failed: {e}") from e
        raw_msg = raw_and_parsed.get("raw")
        plan = raw_and_parsed.get("parsed")
        parsing_error = raw_and_parsed.get("parsing_error")
        if parsing_error is not None:
            raise RuntimeError(f"Planner REDUCE parsing error: {parsing_error}") from parsing_error
        if plan is None:
            raise RuntimeError("Planner REDUCE returned no parsed plan")
        finish_reason = None
        if raw_msg is not None and hasattr(raw_msg, "response_metadata"):
            finish_reason = (raw_msg.response_metadata or {}).get("finish_reason")
        if finish_reason == "length":
            raise RuntimeError(
                "Planner REDUCE output truncated (finish_reason=length); "
                "raising to trigger fallback model."
            )
        logger.info(
            f"[planner] REDUCE complete: {len(plan.chapters)} chapters + "
            f"{len(plan.unused_files)} unused_files (reasoning: {plan.reasoning[:80]})"
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
        files_content = await _load_chapter_files(storage, study_root, chapter.assigned_files)
        adjustments: list[str] = []
        history: list[tuple[ChapterSynthesis, GraderEvaluation]] = []  # all iterations
        best_synthesis: ChapterSynthesis | None = None
        best_eval: GraderEvaluation | None = None
        _REGRESSION_EPSILON = 0.01  # tolerance for grader noise
        for iteration in range(MAX_SELF_REFINE_ITERATIONS):
            # 1. Synthesize
            try:
                synthesis = await _synthesize_attempt(
                    chapter = chapter,
                    files_content = files_content,
                    framework = framework,
                    tone_block = tone_block,
                    previous_adjustments = adjustments,
                    llm = llm,
                )
            except Exception as e:
                raise RuntimeError(
                    f"Synthesizer failed on chapter {chapter.number} iter {iteration}: {e}"
                ) from e
            # 2. Grade
            try:
                evaluation = await _grade_attempt(
                    synthesis_text = synthesis.content,
                    chapter = chapter,
                    user_profile = user_profile,
                    framework = framework,
                    llm = llm,
                )
            except Exception as e:
                raise RuntimeError(
                    f"Grader failed on chapter {chapter.number} iter {iteration}: {e}"
                ) from e
            history.append((synthesis, evaluation))
            logger.info(
                f"[synth][ch{chapter.number:02d}] iter {iteration} — "
                f"score={evaluation.weighted_score:.2f} action={evaluation.action} "
                f"issues={len(evaluation.specific_issues)}"
            )
            # Track argmax — first iteration bootstraps `best_*`.
            if best_eval is None or evaluation.weighted_score > best_eval.weighted_score:
                best_synthesis = synthesis
                best_eval = evaluation
            # 3. Decide
            accept_threshold = user_profile.acceptance_threshold
            if evaluation.action == "accept" or evaluation.weighted_score >= accept_threshold:
                # Accepted — `best_*` already tracks this iteration (argmax)
                break
            # Early-stop on regression (compared to previous iter, not all-time best).
            # Huang 2024 §3.3: further rounds compound drift after a first drop.
            if iteration > 0:
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
        if best_synthesis is None or best_eval is None:
            raise RuntimeError(
                f"Chapter {chapter.number} produced no synthesis after "
                f"{MAX_SELF_REFINE_ITERATIONS} iterations"
            )
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

        # 2) Populate cache ONLY when the chapter was accepted above threshold.
        #    Below-threshold "best effort" chapters should NOT poison the cache
        #    for future runs — they stay in study_root with DEBT flag but are
        #    regenerated on the next same-identity run.
        if accepted_above_threshold:
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
        curated_count = 0
        for n, content in chapters:
            try:
                resp = await chain.ainvoke({
                    "chapter_number": n,
                    "framework": framework,
                    "tone_block": tone_block,
                    "glossary": glossary_str,
                    "chapter_content": content,
                })
            except Exception as e:
                # Best-effort: if one chapter's curation fails, keep the original
                # and move on. Don't crash the whole study.
                logger.warning(
                    f"[curator][ch{n:02d}] curation failed ({e}); keeping original"
                )
                continue
            curated = resp.content if hasattr(resp, "content") else str(resp)
            # Safety: if curator returned something dramatically shorter than the
            # original, it probably refused/truncated — fall back to original.
            if len(curated.strip()) < 0.5 * len(content.strip()):
                logger.warning(
                    f"[curator][ch{n:02d}] output shrank drastically "
                    f"({len(curated)} < 0.5×{len(content)}); keeping original"
                )
                continue
            # Write back to the same MinIO key
            await storage.write(
                f"{study_root}/chapter{n:02d}/README.md",
                curated.strip() + "\n",
                content_type = "text/markdown",
            )
            curated_count += 1
            logger.info(
                f"[curator][ch{n:02d}] normalized "
                f"({len(content)}B → {len(curated)}B)"
            )

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
            raise FileNotFoundError(
                f"no chapter READMEs under {study_root!r} — synthesize phase didn't run"
            )
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
        bundles = _build_chapter_bundles(chapters)
        chain = CRITIC_PROMPT | llm.with_structured_output(
            CriticAssessment,
            method = "function_calling",
        )
        try:
            llm_assessment: CriticAssessment = await chain.ainvoke({
                "framework": framework,
                "file_slugs": ", ".join(sorted(available_slugs)),
                "chapter_bundles": bundles,
            })
        except Exception as e:
            raise RuntimeError(f"Critic LLM call failed: {e}") from e

        # 4) Deterministic style linter — cheap, LLM-free, catches what the
        #    LLM critic is bad at (heading-depth drift, code-density spread,
        #    stub chapters). Runs over the same chapters the LLM just judged.
        linter_issues = _deterministic_linter(chapters)
        if linter_issues:
            logger.info(f"[critic] linter: {len(linter_issues)} style issues flagged")

        # 5) Merge — override citation_coverage with our deterministic value; recompute overall.
        merged_issues = list(llm_assessment.issues) + citation_issues + linter_issues
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
    # Conditional Edges
    # =========================================================================
    def fan_out_chapters(
        self,
        state: KnowledgeDistillerState) -> list[Send]:
        """
        Conditional-edge function. After planner produces state['plan'],
        emit one Send() per chapter so synthesize_chapter runs in parallel
        for N chapters (N ∈ [4, 12]).

        Each Send carries a MINIMAL payload — just what one worker needs.
        Workers share nothing except what they return via the `operator.add`
        reducer on state['synthesis_results'].

        `profile_hash` is computed ONCE here and threaded into every worker
        so the synthesis cache's keys stay consistent across all N chapters.
        """
        user_profile = state["user_profile"]
        profile_dict = (
            user_profile.model_dump()
            if hasattr(user_profile, "model_dump")
            else dict(user_profile)
        )
        profile_hash = canonical_profile_hash(profile_dict)
        version = state.get("version") or "latest"
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
            for ch in state["plan"]
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
        max_concurrent_chapters: int = 5):
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
            async with synth_semaphore:
                return await self.synthesize_chapter(payload, effective_synth, storage, cache)

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
        workflow.add_node("synthesize_chapter", _synthesize_chapter)
        workflow.add_node("curator", _curator)
        workflow.add_node("critic", _critic)
        workflow.add_node("assembler", _assembler)

        # --------------------------------------------------------------------
        # Entry point + linear edges
        # Pipeline: ingest → planner → [fan-out synthesize_chapter ×N]
        #           → curator → critic → assembler → END
        # Curator runs BEFORE critic so the critic judges the final
        # (style-normalized) text, not the raw drafts — otherwise the
        # curator's post-critic rewrites could silently drift facts.
        # --------------------------------------------------------------------
        workflow.set_entry_point("ingest")
        workflow.add_edge("ingest", "planner")

        # Dynamic fan-out: planner → N synthesize_chapter workers via Send()
        workflow.add_conditional_edges(
            "planner",
            self.fan_out_chapters,
            ["synthesize_chapter"],   # list of possible target node names
        )

        # Merge: LangGraph waits for ALL N workers (operator.add reducer accumulates
        # synthesis_results) before firing this edge. Fan-in is automatic.
        workflow.add_edge("synthesize_chapter", "curator")
        workflow.add_edge("curator", "critic")
        workflow.add_edge("critic", "assembler")
        workflow.add_edge("assembler", END)

        return workflow.compile(checkpointer = checkpointer)
