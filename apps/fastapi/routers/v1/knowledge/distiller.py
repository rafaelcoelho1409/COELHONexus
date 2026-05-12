"""
Knowledge Distiller — FastAPI Router

Public API for the KD pipeline. Thin HTTP layer on top of the Celery task
(tasks/knowledge/distiller.py) and MinIO storage (services/knowledge/storage.py).

Endpoints:
  POST   /studies                             Create study (runs scope gate first)
  POST   /studies/resolve                     Resolve canonical docs_url + tier (1-4); supports crossover + coalescing
  POST   /studies/batch                       Enqueue N coalesced studies as a Celery chain (sequential)
  GET    /studies/batch/{batch_id}            Aggregate per-study status for a batch
  GET    /studies/{study_id}                  Status + phase + progress
  GET    /studies/{study_id}/stream           SSE node-by-node updates
  GET    /studies/{study_id}/tree             MinIO object manifest
  GET    /studies/{study_id}/chapters/{n}     One chapter's 3 artifacts
  DELETE /studies/{study_id}                  Cancel
  GET    /downloads/{user_id}/{slug}          .tar.gz of study artifacts (by MinIO folder)

ARCHITECTURE:
  1. POST runs the scope classifier (Groq llama-3.1-8b-instant) SYNCHRONOUSLY
     (~500ms). Off-topic requests return 400 before any Celery work.
  2. On pass, a study_id (uuid4) is generated, study_root is precomputed, and
     a study record is stashed in Redis (coelhonexus:knowledge:study:{id}).
  3. The Celery task is enqueued with task_id = study_id so both identifiers
     stay in lockstep.
  4. The GET endpoints read the Redis record, probe Celery's AsyncResult for
     the latest progress meta, and read MinIO for on-disk artifacts.

STREAMING:
  The SSE endpoint polls AsyncResult.info every 1s (so it's Celery-free —
  no pub/sub infra). It yields a new SSE event whenever the meta dict
  changes. Close the stream when state reaches SUCCESS or FAILURE.

FRAMEWORK GATE:
  The curated catalog (apps/fastapi/files/sources.yaml) is the single
  source of truth. Names not in the catalog → HTTP 404. The previous
  LLM-based scope classifier was removed — every catalog entry is
  pre-vetted as a code framework, so the LLM round-trip was redundant.
  Language is derived deterministically from `entry.category`.
"""
import asyncio
import io
import json
import logging
import tarfile
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Path
from fastapi.responses import StreamingResponse
from celery.result import AsyncResult

import httpx

from celery_app import app as celery_app
from schemas.knowledge.inputs import (
    CreateBatchRequest,
    CreateStudyRequest,
)
from schemas.knowledge.resolver import ResolveRequest, ResolvedStudy
from services.resolver import lookup as resolver_lookup


logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Redis helpers — study_id ↔ study_root registry
# =============================================================================
# 30 days: after a study completes, artifacts live in MinIO regardless. The
# registry is the index for surfacing them back by study_id.
STUDY_TTL_SECONDS = 30 * 24 * 60 * 60


def _study_key(study_id: str) -> str:
    return f"coelhonexus:knowledge:study:{study_id}"


def _batch_key(batch_id: str) -> str:
    return f"coelhonexus:knowledge:batch:{batch_id}"


def _make_study_root(
    user_id: str,
    framework: str,
    version: str | None,
    level: str | None = None) -> str:
    """
    Build the MinIO key prefix for one run.

    Shape: {user_id}/knowledge/{framework}-{version|"latest"}-{level|"senior"}

    Idempotent: the same (user, framework, version, level) tuple always
    maps to the same folder. Re-running a study continues in place, reusing
    every page already written via the streaming ingest cache layer —
    which is the whole point of having a cache. No timestamp component =
    no "stale copies" to sweep up later, and subsequent runs get near-zero
    cache restore cost (study_root *is* the cache for this identity).
    """
    framework_part = framework.lower().strip().replace(" ", "-")
    version_part = (version or "latest").lower().strip().replace(" ", "-")
    level_part = (level or "senior").lower().strip()
    return f"{user_id}/knowledge/{framework_part}-{version_part}-{level_part}"


def _make_group_study_root(
    user_id: str,
    canonical_names: list[str],
    version: str | None,
    level: str | None = None) -> str:
    """
    MinIO prefix for a coalesced ResolvedStudy with ≥2 members.

    Shape (len ≥ 2): {user_id}/knowledge/{primary}-plus{N-1}-{version}-{level}
    Shape (len == 1): same as _make_study_root (delegates — preserves the
    idempotent cache-restore property for solo studies in a batch).

    Example: ["DeepAgents","LangChain","LangGraph"] v=None l="senior"
          → "{user}/knowledge/deepagents-plus2-latest-senior"

    The rule uses the FIRST canonical_name as the primary label (same
    position the resolver's `primary_docs_url` comes from). Stable ordering
    matters: re-running the same coalesced group resolves to the same
    MinIO prefix and hits the cache.
    """
    if len(canonical_names) <= 1:
        return _make_study_root(
            user_id = user_id,
            framework = canonical_names[0] if canonical_names else "unknown",
            version = version,
            level = level,
        )
    primary = canonical_names[0].lower().strip().replace(" ", "-")
    version_part = (version or "latest").lower().strip().replace(" ", "-")
    level_part = (level or "senior").lower().strip()
    return f"{user_id}/knowledge/{primary}-plus{len(canonical_names) - 1}-{version_part}-{level_part}"


async def _save_study_record(redis_aio, study_id: str, record: dict) -> None:
    """Persist {study_id → study_root + metadata} with TTL."""
    await redis_aio.set(
        _study_key(study_id),
        json.dumps(record),
        ex = STUDY_TTL_SECONDS,
    )


async def _load_study_record(redis_aio, study_id: str) -> dict | None:
    raw = await redis_aio.get(_study_key(study_id))
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


async def _save_batch_record(redis_aio, batch_id: str, record: dict) -> None:
    """Persist {batch_id → batch manifest} with the same TTL as studies."""
    await redis_aio.set(
        _batch_key(batch_id),
        json.dumps(record),
        ex = STUDY_TTL_SECONDS,
    )


async def _load_batch_record(redis_aio, batch_id: str) -> dict | None:
    raw = await redis_aio.get(_batch_key(batch_id))
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


def _celery_snapshot(study_id: str) -> dict:
    """
    Read the Celery task state + meta for this study. Returns a uniform shape
    so every status endpoint returns the same keys.

    Progress meta from the Celery task matches:
      {study_id, study_root, phase, last_node, nodes_seen}
    (see tasks/knowledge/distiller.py).
    """
    result = AsyncResult(study_id, app = celery_app)
    snapshot: dict = {
        "task_state": result.status,
        "progress": None,
        "result": None,
        "error": None,
    }
    info = result.info
    if result.status in ("PROGRESS", "STARTED"):
        snapshot["progress"] = info if isinstance(info, dict) else None
    elif result.status == "SUCCESS":
        snapshot["result"] = result.result
    elif result.status == "FAILURE":
        # result.result on failure is the exception object
        snapshot["error"] = str(result.result) if result.result is not None else "unknown error"
    return snapshot


# =============================================================================
# POST /studies/resolve — canonical docs-URL + tier classification
# =============================================================================
# Accepts a framework name OR a crossover request ("Grafana Alloy + LGTM +
# PromQL"), decomposes it into canonical topics, fans out per topic, and
# returns one ResolvedDocs per topic — each carrying a tier (1-4) that tells
# the crawler which ingestion strategy to run.
#
# Stages per topic:
#   A — Registry hint    (PyPI / npm / crates.io — existence + homepage)
#   B — Search           (Exa → Tavily → Jina fallback, ~10 candidates)
#   C — LLM rerank       (strict JSON schema, picks canonical docs_url)
#   D — Content validator (probe /llms-full.txt, /llms.txt, /sitemap.xml +
#                          root liveness D0 + index spot-check D2 + GitHub
#                          discovery upgrade for github.com/{org}/{repo})
#
# Cache: Redis with confidence-based TTL (7 days on conf ≥ 0.7, else 1 hour).
# Reference: docs/KNOWLEDGE-DISTILLER-RESOLVER-STRATEGY.md
# =============================================================================
# DEBUG — GET /studies/_debug/langfuse — verify telemetry wiring
# =============================================================================
# OP-44 hardening (2026-04-25): one-shot probe + flush. Use to diagnose
# "why don't I see traces in the UI?" without exec'ing into pods.
#
# Behavior:
#   1. Reports env-var presence + langfuse-host + auth-check status
#   2. Sends a synthetic test trace tagged `debug-probe` (no LLM call)
#   3. Force-flushes the queue
#   4. Returns the trace_id so you can search for it in the UI
@router.get("/studies/_debug/langfuse")
async def debug_langfuse():
    from services.knowledge.langfuse_client import (
        probe_langfuse,
        flush_langfuse,
        build_langfuse_handler,
    )
    result = probe_langfuse()
    if not result["enabled"] or not result["auth_ok"]:
        return result
    # Send a probe trace via the SAME path real KD calls use:
    # ChatPromptTemplate + ChatOpenAI + langfuse_config callback. This
    # exercises the production code path end-to-end (handler creation
    # + LangChain callback propagation + flush).
    import os
    try:
        h = build_langfuse_handler()
        result["handler_built"] = h is not None
        if h is None:
            result["test_trace_error"] = "handler build returned None"
            return result
        # Smallest possible LangChain call that exercises the callback path.
        # Uses Gemini (cheap, fast, free tier). Works even when KD pipeline
        # isn't running. If this trace appears in LangFuse UI, the entire
        # callback chain is sound.
        from langchain_openai import ChatOpenAI
        from langchain_core.prompts import ChatPromptTemplate
        cfg = {
            "callbacks": [h],
            "metadata": {"source": "debug_langfuse_endpoint"},
            "tags": ["debug-probe"],
        }
        gemini_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if gemini_key:
            llm = ChatOpenAI(
                model = "gemini-2.5-flash",
                api_key = gemini_key,
                base_url = "https://generativelanguage.googleapis.com/v1beta/openai/",
                temperature = 0,
                max_tokens = 8,
            )
            prompt = ChatPromptTemplate.from_messages([
                ("human", "Reply with exactly the word: PONG"),
            ])
            chain = prompt | llm
            resp = await chain.ainvoke({}, config = cfg)
            result["llm_response"] = resp.content[:30] if hasattr(resp, "content") else str(resp)[:30]
        else:
            result["llm_response"] = "(GOOGLE_API_KEY not set; skipped LLM probe — handler still wired)"
        flush_langfuse(reason = "debug-endpoint")
        result["test_trace_sent"] = True
        result["where_to_look"] = (
            f"Filter UI traces by tag='debug-probe' at {result['host']}"
        )
    except Exception as e:
        result["test_trace_error"] = f"{type(e).__name__}: {e}"
    return result


@router.post("/studies/resolve")
async def resolve_study(
    payload: ResolveRequest,
    request: Request):
    """
    DEPRECATED — pending refactor.

    The old multi-stage resolver (registry + search + LLM rerank + tier
    probes) has been replaced by the curated-list resolver at
    POST /api/v1/knowledge/resolve, which reads from sources.yaml.

    The next refactor step rewires this endpoint to:
      1. Look the framework up via services.resolver.lookup()
      2. Build a ResolvedStudy directly from the SourceEntry tiers
      3. Hand off to ingestion as before

    Until that work lands, callers should use POST /api/v1/knowledge/resolve
    and pass the returned URLs into POST /studies directly.
    """
    raise HTTPException(
        status_code = 503,
        detail = (
            "Endpoint pending refactor — use POST /api/v1/knowledge/resolve "
            "for the new curated-list resolver."
        ),
    )


# =============================================================================
# POST /studies — create + scope-gate + enqueue (docs_url REQUIRED)
# =============================================================================
@router.post("/studies")
async def create_study(
    payload: CreateStudyRequest,
    request: Request,
    max_concurrent_chapters: int = 2,  # OP-20: K=2 fits NIM free-tier 40 RPM/model without stampede.
    stop_after: str | None = None):
    """
    Create a Knowledge Distiller study. Framework name must exist in the
    curated catalog (apps/fastapi/files/sources.yaml).

    Steps:
      1. Resolver lookup: derive docs_url, tier, language, github metadata
         from sources.yaml. Language comes from the catalog category — no
         LLM scope gate (curated entries are pre-vetted as code frameworks).
      2. Generate study_id (uuid4), compute study_root prefix.
      3. Stash registry entry in Redis.
      4. Enqueue Celery task (task_id == study_id).

    Query params:
        max_concurrent_chapters (int, default 2): cap on parallel
        synthesize_chapter workers. Lower = more consistent voice across
        chapters; values 1-3 recommended.
        stop_after (str, default None): debug knob. When set to a node
        name (planner / canary_synth / synthesize_chapter / curator /
        critic / assembler), the graph runs up to and including that
        node, then halts. State is checkpointed; downstream nodes can
        be invoked one at a time via POST
        /studies/{study_id}/debug/run_node. Useful for iterating on a
        single node without re-running upstream work.

    Error codes:
        404: framework name not in sources.yaml catalog.
    """
    app = request.app

    # 1) Resolver lookup — must hit the curated catalog.
    entry = resolver_lookup(payload.framework)
    if entry is None or not entry.tiers:
        raise HTTPException(
            status_code = 404,
            detail = {
                "message": (
                    f"'{payload.framework}' is not in the curated catalog. "
                    "GET /api/v1/knowledge/resolve/sources lists every "
                    "available technology."
                ),
                "framework": payload.framework,
            },
        )
    docs_url = entry.best.url
    tier = entry.best.tier
    repo_url = entry.github_repo
    github_org, github_repo = entry.github_org_repo

    # 2) Build study identity. Language comes from the catalog (entry.language),
    #    derived deterministically from the category field — no LLM round-trip.
    study_id = str(uuid.uuid4())
    study_root = _make_study_root(
        user_id = payload.user_id,
        framework = entry.name,
        version = payload.version,
        level = payload.user_profile.level,
    )
    record = {
        "study_id": study_id,
        "study_root": study_root,
        "user_id": payload.user_id,
        "framework": entry.name,
        "version": payload.version or "latest",
        "level": payload.user_profile.level,
        "language": entry.language,
        "category": entry.category,
        "docs_url": docs_url,
        "docs_url_source": "catalog",
        "tier": tier,
        "tier_kind": entry.best.kind,
        "available_tiers": [
            {"tier": t.tier, "kind": t.kind, "url": t.url} for t in entry.tiers
        ],
        "github_org": github_org,
        "github_repo": github_repo,
        "github_default_branch": None,  # discovered by Celery task if needed
        "repo_url": repo_url,
        "user_profile": payload.user_profile.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # 4) Persist study registry entry.
    await _save_study_record(app.state.redis_aio, study_id, record)

    # 5) Enqueue Celery task(s). Auto-chain ingestion → distiller when the
    #    corpus cache is empty so the user keeps the one-click UX:
    #      cache hit  → distiller alone
    #      cache miss → chain(ingestion, distiller)
    #    Both signatures are .si() (immutable) so the chain doesn't pass
    #    intermediate results between tasks.
    from celery import chain as celery_chain
    from services.knowledge.cache import StudyCache
    from tasks.knowledge.distiller import run_knowledge_distiller
    from tasks.knowledge.ingestion import run_knowledge_ingestion

    cache = StudyCache(storage = app.state.study_storage, latest_ttl_days = 14)
    cache_hit = await cache.get_ingestion(entry.name, payload.version)

    distiller_kwargs = {
        "study_id": study_id,
        "framework": entry.name,
        "version": payload.version,
        "docs_url": docs_url,
        "language": entry.language,
        "user_id": payload.user_id,
        "user_profile": payload.user_profile.model_dump(),
        "study_root": study_root,
        "max_concurrent_chapters": max_concurrent_chapters,
        "tier": tier,
        "github_discover": "homepage" if repo_url else None,
        "github_org": github_org,
        "github_repo": github_repo,
        "github_default_branch": None,
        "repo_url": repo_url,
        "preview": payload.preview,
        "skip_below_threshold": payload.skip_below_threshold,
        "stop_after": stop_after,
    }

    if cache_hit is None:
        ingestion_task_id = str(uuid.uuid4())
        ingestion_sig = run_knowledge_ingestion.si(
            study_id = ingestion_task_id,
            framework = entry.name,
            version = payload.version,
            docs_url = docs_url,
            language = entry.language,
            user_id = payload.user_id,
            study_root = study_root,
            tier = tier,
            github_discover = "homepage" if repo_url else None,
            github_org = github_org,
            github_repo = github_repo,
            github_default_branch = None,
            repo_url = repo_url,
        ).set(task_id = ingestion_task_id, expires = 3600)
        distiller_sig = run_knowledge_distiller.si(**distiller_kwargs).set(
            task_id = study_id, expires = 7200,
        )
        celery_chain(ingestion_sig, distiller_sig).apply_async()
        logger.info(
            f"[knowledge] cache miss — chained ingestion={ingestion_task_id} "
            f"→ distiller={study_id} for framework={entry.name} tier={tier}"
        )
    else:
        run_knowledge_distiller.apply_async(
            kwargs = distiller_kwargs,
            task_id = study_id,
            expires = 7200,
        )
        logger.info(
            f"[knowledge] cache hit — distiller queued direct: id={study_id} "
            f"framework={entry.name} cached_at={cache_hit.cached_at}"
        )

    return {
        "study_id": study_id,
        "task_id": study_id,
        "study_root": study_root,
        "status": "queued",
        "endpoint": f"/api/v1/tasks/{study_id}",
        "stream_endpoint": f"/api/v1/knowledge/studies/{study_id}/stream",
        "framework": entry.name,
        "category": entry.category,
        "language": entry.language,
        "tier": tier,
        "tier_kind": entry.best.kind,
        "docs_url": docs_url,
        "available_tiers": [
            {"tier": t.tier, "kind": t.kind, "url": t.url} for t in entry.tiers
        ],
    }


# =============================================================================
# POST /studies/batch — enqueue N coalesced studies sequentially via chain
# =============================================================================
# Consumes the `studies[]` list returned by POST /studies/resolve. The
# client reviews / edits / confirms the coalesced groups, then POSTs them
# here. Each ResolvedStudy in the list becomes one Celery task linked via
# celery.chain(...) — strict sequential execution (no cross-study LLM
# rate-limit thrashing, no target-host 429 bursts). A coalesced group of
# ≥2 members materializes as ONE study whose MinIO prefix + manifest
# carry all the canonical_names (the coalescer's original intent).
#
# Failure isolation: each link runs with `is_chained=True`, which converts
# top-level exceptions inside the Celery task into a sentinel success
# result so the chain keeps moving. Standard workaround for celery#2416
# ("continue chain on failure after retries" is not a built-in feature).
#
# Ordering inside the chain: tier ascending (Tier 1 → Tier 4 → GH). Fast
# studies complete first so the user sees artifacts while the slow tail
# runs. Within a tier, coalesced groups (higher info-per-minute) go first.
@router.post("/studies/batch")
async def create_batch(
    payload: CreateBatchRequest,
    request: Request):
    """
    Enqueue a batch of studies (possibly coalesced) as a Celery chain
    for strict sequential execution.

    Returns:
        {
          "batch_id": <uuid>,
          "total_studies": N,
          "user_id": ...,
          "studies": [
            {
              "study_id": <uuid>,
              "canonical_names": [...],
              "primary_docs_url": ...,
              "tier": 1..4,
              "coalesced_from": 1..N,
              "study_root": ...,
              "status": "queued"
            },
            ...
          ]
        }

    The per-study `study_id` can be polled individually via
    GET /studies/{study_id}; the whole batch via GET /studies/batch/{batch_id}.
    """
    from celery import chain
    from services.knowledge.cache import StudyCache
    from tasks.knowledge.distiller import run_knowledge_distiller
    from tasks.knowledge.ingestion import run_knowledge_ingestion

    app = request.app
    user_profile_dump = payload.user_profile.model_dump()
    cache = StudyCache(storage = app.state.study_storage, latest_ttl_days = 14)

    # Order the studies: lower tier first (fast-path first). Coalesced groups
    # within the same tier go ahead of solo ones (more information per unit
    # of wall-clock time). Preserve original index as tiebreaker so the
    # ordering is deterministic for a given input.
    ordered_studies: list[tuple[int, ResolvedStudy]] = sorted(
        enumerate(payload.studies),
        key = lambda pair: (
            pair[1].tier,
            -pair[1].coalesced_from,  # higher coalesced_from first within tier
            pair[0],
        ),
    )

    signatures: list = []
    manifest: list[dict] = []
    batch_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    for _, study in ordered_studies:
        study_id = str(uuid.uuid4())
        names = study.canonical_names or []
        if not names:
            # Defensive: a ResolvedStudy with no canonical_names is malformed
            # upstream. Skip with a visible skip entry rather than crashing
            # the whole batch.
            manifest.append({
                "study_id": None,
                "canonical_names": [],
                "primary_docs_url": study.primary_docs_url,
                "tier": study.tier,
                "coalesced_from": study.coalesced_from,
                "study_root": None,
                "status": "skipped",
                "reason": "empty canonical_names",
            })
            continue

        joined_framework = " + ".join(names)
        study_root = _make_group_study_root(
            user_id = payload.user_id,
            canonical_names = names,
            version = study.version,
            level = payload.user_profile.level,
        )

        # Persist per-study record so GET /studies/{study_id} works for each
        # batch member exactly like a single-study POST /studies creation.
        record = {
            "study_id": study_id,
            "study_root": study_root,
            "user_id": payload.user_id,
            "framework": joined_framework,
            "canonical_names": names,
            "version": study.version,
            "level": payload.user_profile.level,
            "language": None,
            "docs_url": study.primary_docs_url,
            "docs_urls": study.docs_urls,
            "docs_url_source": "batch",
            "tier": study.tier,
            "github_discover": None,
            "github_org": None,
            "github_repo": None,
            "github_default_branch": None,
            "repo_url": study.repo_urls[0] if study.repo_urls else None,
            "user_profile": user_profile_dump,
            "coalesced_from": study.coalesced_from,
            "batch_id": batch_id,
            "created_at": now,
        }
        await _save_study_record(app.state.redis_aio, study_id, record)

        # Cache check — if the corpus for this framework + version isn't
        # in `_cache/ingestion/...`, prepend an ingestion task to the chain
        # so the distiller has corpus to read. Mirrors the /studies path.
        cache_hit = await cache.get_ingestion(joined_framework, study.version)
        if cache_hit is None:
            ingestion_task_id = str(uuid.uuid4())
            ingestion_sig = run_knowledge_ingestion.si(
                study_id = ingestion_task_id,
                framework = joined_framework,
                version = study.version,
                docs_url = study.primary_docs_url,
                language = None,
                user_id = payload.user_id,
                study_root = study_root,
                tier = study.tier,
                github_discover = None,
                github_org = None,
                github_repo = None,
                github_default_branch = None,
                repo_url = study.repo_urls[0] if study.repo_urls else None,
            ).set(task_id = ingestion_task_id, expires = 3600)
            signatures.append(ingestion_sig)

        # Immutable signature (.si) — the chain passes the previous task's
        # result as first arg by default; .si ignores it so our kwargs-only
        # task signature works cleanly. .set(task_id=...) pins the Celery
        # task_id to study_id so AsyncResult(study_id) works the same as it
        # does for the single-study /studies POST path.
        sig = run_knowledge_distiller.si(
            study_id = study_id,
            framework = joined_framework,
            version = study.version,
            docs_url = study.primary_docs_url,
            language = None,
            user_id = payload.user_id,
            user_profile = user_profile_dump,
            study_root = study_root,
            max_concurrent_chapters = payload.max_concurrent_chapters,
            tier = study.tier,
            github_discover = None,
            github_org = None,
            github_repo = None,
            github_default_branch = None,
            repo_url = study.repo_urls[0] if study.repo_urls else None,
            docs_urls = study.docs_urls,
            canonical_names = names,
            is_chained = True,
        ).set(task_id = study_id, expires = 7200)

        signatures.append(sig)
        manifest.append({
            "study_id": study_id,
            "canonical_names": names,
            "primary_docs_url": study.primary_docs_url,
            "tier": study.tier,
            "coalesced_from": study.coalesced_from,
            "study_root": study_root,
            "status": "queued",
        })

    if not signatures:
        raise HTTPException(
            status_code = 422,
            detail = {
                "message": "No valid studies in the batch — every entry was skipped.",
                "manifest": manifest,
            },
        )

    # Kick off the chain. With `.si()` each link runs as a fresh call,
    # ignoring the prior task's return value. The worker picks up the
    # next link only when the prior one reports SUCCESS (including the
    # sentinel-failure SUCCESS from is_chained=True wrapping).
    chain(*signatures).apply_async()

    batch_record = {
        "batch_id": batch_id,
        "user_id": payload.user_id,
        "created_at": now,
        "total_studies": len(manifest),
        "studies": manifest,
    }
    await _save_batch_record(app.state.redis_aio, batch_id, batch_record)

    logger.info(
        f"[knowledge] batch queued: batch_id={batch_id} "
        f"studies={len(signatures)} (skipped={len(manifest) - len(signatures)}) "
        f"user_id={payload.user_id}"
    )

    return {
        "batch_id": batch_id,
        "total_studies": len(manifest),
        "user_id": payload.user_id,
        "studies": manifest,
        "batch_endpoint": f"/api/v1/knowledge/studies/batch/{batch_id}",
    }


# =============================================================================
# GET /studies/batch/{batch_id} — aggregate per-study status
# =============================================================================
# Per-member status is read from each study's Celery AsyncResult (same
# source as /studies/{study_id}), so this endpoint is a thin aggregator
# over the manifest stored at batch creation time.
#
# Route precedence: this path must be declared BEFORE GET /studies/{study_id}
# so FastAPI matches the literal "batch" segment first.
@router.get("/studies/batch/{batch_id}")
async def get_batch_status(
    batch_id: str = Path(..., description = "Batch UUID returned by POST /studies/batch"),
    request: Request = None):
    """
    Aggregate status for a batch. Returns the manifest plus a live
    Celery snapshot per member (task_state, progress, result / error).
    """
    record = await _load_batch_record(request.app.state.redis_aio, batch_id)
    if not record:
        raise HTTPException(
            status_code = 404,
            detail = {"message": "batch not found (may have expired)", "batch_id": batch_id},
        )

    enriched_studies: list[dict] = []
    for entry in record.get("studies", []):
        study_id = entry.get("study_id")
        item = dict(entry)
        if study_id:
            snap = _celery_snapshot(study_id)
            # Map Celery state + our sentinel-failure phase into a single
            # user-facing status the frontend can render without branching.
            task_state = snap.get("task_state")
            result = snap.get("result") or {}
            failure_sentinel = isinstance(result, dict) and result.get("phase") == "failed"
            if task_state == "SUCCESS" and failure_sentinel:
                derived = "failed"
            elif task_state == "SUCCESS":
                derived = "complete"
            elif task_state == "FAILURE":
                derived = "failed"
            elif task_state in ("PROGRESS", "STARTED"):
                derived = "running"
            elif task_state in ("PENDING", "RECEIVED"):
                derived = "queued"
            else:
                derived = str(task_state or "unknown").lower()
            item["status"] = derived
            item["task_state"] = task_state
            item["progress"] = snap.get("progress")
            item["result"] = snap.get("result")
            item["error"] = snap.get("error") or (
                result.get("error") if failure_sentinel else None
            )
        enriched_studies.append(item)

    # Derived batch-level status: "complete" when every child is terminal
    # (complete OR failed), otherwise "running" if any is running/queued.
    statuses = {s.get("status") for s in enriched_studies}
    if statuses.issubset({"complete", "failed", "skipped"}):
        batch_status = "complete"
    else:
        batch_status = "running"

    return {
        **record,
        "studies": enriched_studies,
        "status": batch_status,
    }


# =============================================================================
# GET /studies/{study_id} — status + phase + Celery meta
# =============================================================================
@router.get("/studies")
async def list_studies(
    request: Request,
    limit: int = 50):
    """
    List recent studies from the Redis registry, newest first.
    Returns a slim payload per study (study_id + framework + status indicator +
    created_at) so the UI can render a table without paging through full
    plan/synth_results blobs.
    """
    redis_aio = request.app.state.redis_aio
    keys = []
    async for k in redis_aio.scan_iter(
        match="coelhonexus:knowledge:study:*", count=200,
    ):
        keys.append(k.decode() if isinstance(k, bytes) else k)
    out = []
    for key in keys:
        try:
            raw = await redis_aio.get(key)
        except Exception:
            continue
        if not raw:
            continue
        try:
            rec = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception:
            continue
        sid = rec.get("study_id")
        if not sid:
            continue
        snapshot = _celery_snapshot(sid)
        out.append({
            "study_id": sid,
            "framework": rec.get("framework"),
            "version": rec.get("version"),
            "level": rec.get("level"),
            "user_id": rec.get("user_id"),
            "study_root": rec.get("study_root"),
            "created_at": rec.get("created_at"),
            "task_state": snapshot.get("task_state"),
            "current_phase": (snapshot.get("progress") or {}).get("phase"),
        })
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"studies": out[:limit], "total": len(out)}


@router.get("/studies/{study_id}")
async def get_study(
    study_id: str,
    request: Request):
    """
    Unified status endpoint. Combines:
      - the study registry entry (framework, study_root, user_profile, etc.)
      - current Celery task state + progress meta from the running task
      - final result if the task has succeeded
    """
    record = await _load_study_record(request.app.state.redis_aio, study_id)
    if not record:
        raise HTTPException(status_code = 404, detail = "Study not found")
    snapshot = _celery_snapshot(study_id)
    return {
        "study": record,
        **snapshot,
    }


# =============================================================================
# GET /studies/{study_id}/stream — SSE node-by-node updates
# =============================================================================
@router.get("/studies/{study_id}/stream")
async def stream_study(
    study_id: str,
    request: Request):
    """
    Server-Sent Events stream of task progress.

    Implementation note: the Celery task lives in a separate worker process.
    Instead of setting up Redis pub/sub, we poll two sources every 1s:
      1. Celery AsyncResult.info — top-level phase (ingest/plan/synthesize/…)
      2. Redis key `coelhonexus:knowledge:ingest_progress:{id}` — per-page
         progress emitted by the tier functions (Step 8). Lets the client
         see "47/153 pages fetched" live during long ingest runs.

    Event frames:
        data: {"event": "progress", "task_state": "...",
               "progress": {...celery meta...},
               "ingest_progress": {tier, current, total, last_url, status}}\\n\\n
        data: {"event": "success",  "result": {...}}\\n\\n
        data: {"event": "failure",  "error":  "..."}\\n\\n
        data: {"event": "end"}\\n\\n

    The stream closes when the task reaches SUCCESS or FAILURE.
    """
    record = await _load_study_record(request.app.state.redis_aio, study_id)
    if not record:
        raise HTTPException(status_code = 404, detail = "Study not found")

    from services.knowledge.ingest_progress import read_progress

    async def event_generator():
        last_progress: dict | None = None
        last_ingest_progress: dict | None = None
        # emit an initial frame so the client has context immediately
        yield f"data: {json.dumps({'event': 'study', 'study': record})}\n\n"
        while True:
            # Stop if the client disconnected — don't keep polling into the void.
            if await request.is_disconnected():
                logger.info(f"[knowledge] stream client disconnected: {study_id}")
                return
            snap = _celery_snapshot(study_id)
            state = snap["task_state"]
            ingest_progress = await read_progress(
                request.app.state.redis_aio, study_id,
            )
            if state == "SUCCESS":
                yield f"data: {json.dumps({'event': 'success', 'result': snap['result']})}\n\n"
                yield f"data: {json.dumps({'event': 'end'})}\n\n"
                return
            if state == "FAILURE":
                yield f"data: {json.dumps({'event': 'failure', 'error': snap['error']})}\n\n"
                yield f"data: {json.dumps({'event': 'end'})}\n\n"
                return
            # PROGRESS / STARTED / PENDING — emit when EITHER the Celery meta
            # or the ingest per-page counter has moved.
            progress = snap.get("progress")
            if progress != last_progress or ingest_progress != last_ingest_progress:
                yield (
                    "data: "
                    + json.dumps({
                        "event": "progress",
                        "task_state": state,
                        "progress": progress,
                        "ingest_progress": ingest_progress,
                    })
                    + "\n\n"
                )
                last_progress = progress
                last_ingest_progress = ingest_progress
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type = "text/event-stream",
        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # nginx/ingress: don't buffer
        },
    )


# =============================================================================
# GET /studies/{study_id}/tree — MinIO manifest
# =============================================================================
@router.get("/studies/{study_id}/tree")
async def get_study_tree(
    study_id: str,
    request: Request):
    """
    List all objects written under this study's MinIO prefix.

    Returns:
        {
            "study_id": ...,
            "study_root": ...,
            "total": N,
            "objects": ["{root}/research/raw/foo.md", ...],
        }
    """
    record = await _load_study_record(request.app.state.redis_aio, study_id)
    if not record:
        raise HTTPException(status_code = 404, detail = "Study not found")
    storage = request.app.state.study_storage
    keys = await storage.list(record["study_root"] + "/")
    return {
        "study_id": study_id,
        "study_root": record["study_root"],
        "total": len(keys),
        "objects": keys,
    }


# =============================================================================
# GET /studies/{study_id}/chapters/{n} — one chapter's 3 artifacts
# =============================================================================
@router.get("/studies/{study_id}/chapters/{n}")
async def get_chapter(
    request: Request,
    study_id: str,
    n: int = Path(..., ge = 1, le = 12)):
    """
    Read one chapter's 3 artifacts from MinIO:
      - {root}/chapter{N:02d}/README.md
      - {root}/chapter{N:02d}/challenges.md
      - {root}/chapter{N:02d}/flashcards.json

    Returns:
        {
            "chapter": N,
            "content":    str,        # README.md
            "challenges": str,        # challenges.md
            "flashcards": list[dict], # parsed flashcards.json
        }

    404 if any of the three artifacts is missing — the chapter is incomplete
    and shouldn't be surfaced.
    """
    record = await _load_study_record(request.app.state.redis_aio, study_id)
    if not record:
        raise HTTPException(status_code = 404, detail = "Study not found")
    storage = request.app.state.study_storage
    chapter_prefix = f"{record['study_root']}/chapter{n:02d}"
    readme_key = f"{chapter_prefix}/README.md"
    challenges_key = f"{chapter_prefix}/challenges.md"
    flashcards_key = f"{chapter_prefix}/flashcards.json"
    try:
        readme, challenges, flashcards_raw = await asyncio.gather(
            storage.read_text(readme_key),
            storage.read_text(challenges_key),
            storage.read_text(flashcards_key),
        )
    except Exception as e:
        logger.info(f"[knowledge] chapter {n} not ready for {study_id}: {e}")
        raise HTTPException(
            status_code = 404,
            detail = f"Chapter {n} not ready or not found",
        )
    try:
        flashcards = json.loads(flashcards_raw)
    except json.JSONDecodeError:
        flashcards = []
    return {
        "study_id": study_id,
        "chapter": n,
        "content": readme,
        "challenges": challenges,
        "flashcards": flashcards,
    }


# =============================================================================
# Tarball helper (shared by UUID + folder-slug endpoints)
# =============================================================================
async def _build_study_tarball(
    storage,
    study_root: str,
    framework_tag: str,
    include_raw: bool) -> tuple[io.BytesIO, str]:
    """
    Fetch all selected artifacts under `study_root` from MinIO and bundle into
    a gzipped tar in memory. Returns (buffer, suggested_filename).

    Raises HTTPException(404) if the prefix is empty or has no user-facing
    artifacts (pipeline still running).
    """
    all_keys = await storage.list(study_root + "/")
    if not all_keys:
        raise HTTPException(
            status_code = 404,
            detail = f"No artifacts found at MinIO prefix {study_root!r}",
        )

    prefix = f"{study_root}/"
    selected: list[tuple[str, str]] = []  # [(minio_key, archive_relpath), ...]
    for key in all_keys:
        rel = key[len(prefix):]
        if rel.startswith("research/raw/") and not include_raw:
            continue
        selected.append((key, rel))
    if not selected:
        raise HTTPException(
            status_code = 404,
            detail = (
                "Study has no user-facing artifacts yet (pipeline incomplete). "
                "Use ?include_raw=true to download raw ingestion if needed."
            ),
        )

    # Parallel-fetch files from MinIO, in batches so we don't storm it.
    async def _fetch(key: str) -> bytes:
        return await storage.read(key)
    batch_size = 20
    fetched: dict[str, bytes] = {}
    for i in range(0, len(selected), batch_size):
        batch = selected[i : i + batch_size]
        results = await asyncio.gather(*(_fetch(k) for k, _ in batch))
        for (k, _), data in zip(batch, results):
            fetched[k] = data
    logger.info(
        f"[knowledge] download {study_root}: {len(fetched)} objects "
        f"({sum(len(v) for v in fetched.values())} bytes)"
    )

    # Build tarball in memory. Root folder inside = study_root basename so
    # `tar -xzf file.tar.gz -C target/` extracts as `target/<folder>/...`.
    root_folder = study_root.rsplit("/", 1)[-1]
    buf = io.BytesIO()
    with tarfile.open(fileobj = buf, mode = "w:gz") as tar:
        for key, rel in selected:
            data = fetched[key]
            info = tarfile.TarInfo(name = f"{root_folder}/{rel}")
            info.size = len(data)
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)

    filename = f"study-{framework_tag}.tar.gz"
    return buf, filename


# =============================================================================
# GET /downloads/{user_id}/{slug} — direct MinIO-folder download
# =============================================================================
# Recommended endpoint for offline reading — no UUID lookup required.
# Uses the MinIO folder name (e.g. "pydantic-20260420T171547Z") directly, so
# you can grab a study as long as its objects are still in the bucket — even
# if the Redis registry entry TTL'd out.
@router.get("/downloads/{user_id}/{slug}")
async def download_by_slug(
    user_id: str,
    slug: str,
    request: Request,
    include_raw: bool = True):
    """
    Download a study's artifacts by MinIO folder name.

    `{slug}` is the folder name under `{user_id}/knowledge/` — typically
    `<framework>-<timestamp>` (e.g. `pydantic-20260420T171547Z`) or
    `<framework>-<version>-<timestamp>`. You can list existing studies via
    `mc ls coelhonexus/{user_id}/knowledge/` or your MinIO console.

    Defaults: includes the full raw corpus under `research/raw/` so the
    downloaded bundle is self-contained and verifiable.

    Filters (query params):
      - `include_raw=false` → skip `research/raw/*.md` (usually 500+ files, much smaller tarball)

    Usage:
      curl -o study.tar.gz \\
        "http://<host>/api/v1/knowledge/downloads/smoketest/pydantic-20260420T171547Z"
      tar -xzf study.tar.gz -C ~/Workbench/STUDIES/Pydantic/
    """
    storage = request.app.state.study_storage
    study_root = f"{user_id}/knowledge/{slug}"
    buf, filename = await _build_study_tarball(
        storage, study_root, slug, include_raw,
    )
    return StreamingResponse(
        buf,
        media_type = "application/gzip",
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(buf.getbuffer().nbytes),
        },
    )


# =============================================================================
# DELETE /studies/{study_id} — cancel running task
# =============================================================================
@router.delete("/studies/{study_id}")
async def cancel_study(
    study_id: str,
    request: Request):
    """
    Cancel a running study. Revokes the Celery task with SIGTERM and marks
    the registry record as cancelled. Does NOT delete MinIO artifacts — any
    already-written chapters remain readable via /tree and /chapters/{n}.

    Returns:
        {"study_id": ..., "status": "cancelled"}
    """
    record = await _load_study_record(request.app.state.redis_aio, study_id)
    if not record:
        raise HTTPException(status_code = 404, detail = "Study not found")
    # Revoke the Celery task — terminate=True sends SIGTERM to the worker.
    celery_app.control.revoke(study_id, terminate = True)
    # Update registry so subsequent GETs reflect the cancellation
    record["status"] = "cancelled"
    record["cancelled_at"] = datetime.now(timezone.utc).isoformat()
    await _save_study_record(request.app.state.redis_aio, study_id, record)
    logger.info(f"[knowledge] study cancelled: {study_id}")
    return {
        "study_id": study_id,
        "status": "cancelled",
    }
