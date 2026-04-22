"""
Knowledge Distiller — FastAPI Router

Public API for the KD pipeline. Thin HTTP layer on top of the Celery task
(tasks/knowledge/distiller.py) and MinIO storage (services/knowledge/storage.py).

Endpoints:
  POST   /studies                             Create study (runs scope gate first)
  POST   /studies/resolve                     Resolve canonical docs_url + tier (1-4); supports crossover
  GET    /studies/{study_id}                  Status + phase + progress
  GET    /studies/{study_id}/stream           SSE node-by-node updates
  GET    /studies/{study_id}/tree             MinIO object manifest
  GET    /studies/{study_id}/chapters/{n}     One chapter's 3 artifacts
  POST   /studies/{study_id}/export           PDF/HTML/EPUB/Anki derived export
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

SCOPE-GATE FAILURE MODES:
  - LLM succeeds + is_code_framework=False → 400 with rejection_reason
  - LLM raises (network, timeout)          → 503 — we fail CLOSED (never
    enqueue unverified scope)
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
    CreateStudyRequest,
    ExportRequest,
)
from schemas.knowledge.resolver import ResolveRequest
from services.knowledge.docs_resolver import resolve as resolve_docs
from services.knowledge.scope import classify_scope


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
#   B — SearXNG          (3 parallel query templates, ~15 candidates)
#   C — LLM rerank       (strict JSON schema, picks canonical docs_url)
#   D — Content validator (probe /llms-full.txt, /llms.txt, /sitemap.xml +
#                          root liveness D0 + index spot-check D2 + GitHub
#                          discovery upgrade for github.com/{org}/{repo})
#
# Cache: Redis with confidence-based TTL (7 days on conf ≥ 0.7, else 1 hour).
# Reference: docs/KNOWLEDGE-DISTILLER-RESOLVER-STRATEGY.md
@router.post("/studies/resolve")
async def resolve_study(
    payload: ResolveRequest,
    request: Request):
    """
    Resolve docs for a single framework OR a crossover request.

    Returns:
        {
          "input": <raw framework arg>,
          "is_crossover": bool,
          "total_topics": int,
          "results": [ResolvedDocs, ...]
        }

    ResolvedDocs carries: canonical_name, docs_url, repo_url, version,
    tier (1-4), tier_evidence (per-file probes + D0 liveness + D2
    spot-check), confidence (0-1), fallback_candidates (rejected URLs
    with reasons), source_signals (provenance).

    Error codes:
        400: scope gate rejected the input.
        503: scope classifier itself failed.
    """
    app = request.app
    # Scope gate + decomposer + rerank all share the resolver LLM chain
    # (same 14-model order as app.state.llm but with 30s Groq / 60s NIM
    # timeouts — stalled primaries cascade in 1 min instead of 5). Groq 8B
    # is excluded as primary because its training cutoff misclassifies
    # 2025-2026 frameworks; NIM GLM-5.1 is the primary, which knows them.
    scope_llm = app.state.llm_resolver

    try:
        scope = await classify_scope(payload.framework, scope_llm)
    except RuntimeError as e:
        logger.warning(f"[knowledge] resolve scope failed: {e}")
        raise HTTPException(
            status_code = 503,
            detail = f"Scope classifier unavailable: {e}",
        )
    if not scope.is_code_framework:
        raise HTTPException(
            status_code = 400,
            detail = {
                "message": scope.rejection_reason or "Not a code framework",
                "detected_topic": scope.detected_topic,
                "framework": payload.framework,
            },
        )

    # Decompose + rerank share the same resolver LLM chain as scope gate.
    rerank_llm = app.state.llm_resolver
    if app.state.search_chain is None:
        raise HTTPException(
            status_code = 503,
            detail = (
                "No search provider keys configured. Set at least one of: "
                "EXA_API_KEY, TAVILY_API_KEY, JINA_API_KEY."
            ),
        )
    results = await resolve_docs(
        request = payload,
        llm = rerank_llm,
        search_chain = app.state.search_chain,
        redis_aio = app.state.redis_aio,
    )

    return {
        "input": payload.framework,
        "is_crossover": len(results) > 1,
        "total_topics": len(results),
        "results": [r.model_dump() for r in results],
    }


# =============================================================================
# POST /studies — create + scope-gate + enqueue (docs_url REQUIRED)
# =============================================================================
@router.post("/studies")
async def create_study(
    payload: CreateStudyRequest,
    request: Request,
    max_concurrent_chapters: int = 5):
    """
    Create a Knowledge Distiller study. Requires `docs_url` — use
    POST /studies/resolve first if you don't already know the URL.

    Steps:
      1. Scope gate (Groq 8B classifier, ~500ms): reject non-code-frameworks.
      2. HEAD-verify docs_url is reachable.
      3. Generate study_id (uuid4), compute study_root prefix.
      4. Stash registry entry in Redis.
      5. Enqueue Celery task (task_id == study_id).

    Query params:
        max_concurrent_chapters (int, default 2): cap on parallel
        synthesize_chapter workers. Lower = more consistent voice across
        chapters (primary LLM serves every chapter), slower overall. Set
        to 1 for strict serialization. Values 1-3 recommended; higher
        values risk rate-limiting the primary model and fanning out to
        different fallbacks → inconsistent tone between chapters.

    Error codes:
        400: scope gate rejected the input. detail contains the rejection_reason.
        422: docs_url not reachable (HEAD returned non-2xx/3xx).
        503: scope classifier itself failed.
    """
    app = request.app
    # 1) Scope gate
    scope_llm = getattr(app.state, "llm_scope", None) or app.state.llm
    try:
        scope = await classify_scope(payload.framework, scope_llm)
    except RuntimeError as e:
        logger.warning(f"[knowledge] scope classifier failed for '{payload.framework}': {e}")
        raise HTTPException(
            status_code = 503,
            detail = f"Scope classifier unavailable: {e}",
        )
    if not scope.is_code_framework:
        raise HTTPException(
            status_code = 400,
            detail = {
                "message": scope.rejection_reason or "Not a code framework",
                "detected_topic": scope.detected_topic,
                "framework": payload.framework,
            },
        )

    # 2) HEAD-verify docs_url is reachable before enqueueing expensive work
    async with httpx.AsyncClient(
        timeout = httpx.Timeout(5.0, connect = 5.0),
        headers = {"User-Agent": "COELHONexus-KnowledgeDistiller/1.0"},
    ) as client:
        reachable = False
        try:
            r = await client.head(payload.docs_url, follow_redirects = True)
            if 200 <= r.status_code < 400:
                reachable = True
            elif r.status_code == 405:
                # Some servers reject HEAD — retry with GET (no body read)
                r = await client.get(payload.docs_url, follow_redirects = True)
                reachable = 200 <= r.status_code < 400
        except (httpx.RequestError, httpx.HTTPError) as e:
            logger.info(f"[knowledge] docs_url HEAD failed for {payload.docs_url}: {e}")
    if not reachable:
        raise HTTPException(
            status_code = 422,
            detail = {
                "message": (
                    "The supplied docs_url is not reachable. Verify the URL "
                    "or call POST /studies/resolve to find one automatically."
                ),
                "docs_url": payload.docs_url,
            },
        )

    # 3) Build study identity — slug = {framework}-{version|"latest"}-{level}-{ts}
    study_id = str(uuid.uuid4())
    study_root = _make_study_root(
        user_id = payload.user_id,
        framework = payload.framework,
        version = payload.version,
        level = payload.user_profile.level,
    )
    record = {
        "study_id": study_id,
        "study_root": study_root,
        "user_id": payload.user_id,
        "framework": payload.framework,
        "version": payload.version or "latest",   # normalize for downstream cache key
        "level": payload.user_profile.level,
        "language": scope.language,
        "detected_topic": scope.detected_topic,
        "docs_url": payload.docs_url,
        "docs_url_source": "user",
        "tier": payload.tier,
        "github_discover": payload.github_discover,
        "github_org": payload.github_org,
        "github_repo": payload.github_repo,
        "github_default_branch": payload.github_default_branch,
        "repo_url": payload.repo_url,
        "user_profile": payload.user_profile.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # 4) Persist study registry entry
    await _save_study_record(app.state.redis_aio, study_id, record)

    # 5) Enqueue Celery task — task_id == study_id so /studies/{id} can read
    #    AsyncResult by the same identifier. Forward the resolver's tier +
    #    GitHub metadata when present so the ingestion dispatcher picks the
    #    right strategy (Tier 1 / 2 / 3 / 4 / Tier-GH); legacy callers that
    #    POST without these fields fall through to Tier 4 Playwright.
    from tasks.knowledge.distiller import run_knowledge_distiller
    run_knowledge_distiller.apply_async(
        kwargs = {
            "study_id": study_id,
            "framework": payload.framework,
            "version": payload.version,
            "docs_url": payload.docs_url,
            "language": scope.language,
            "user_id": payload.user_id,
            "user_profile": payload.user_profile.model_dump(),
            "study_root": study_root,
            "max_concurrent_chapters": max_concurrent_chapters,
            # Resolver hints (all optional, None by default)
            "tier": payload.tier,
            "github_discover": payload.github_discover,
            "github_org": payload.github_org,
            "github_repo": payload.github_repo,
            "github_default_branch": payload.github_default_branch,
            "repo_url": payload.repo_url,
        },
        task_id = study_id,
    )
    logger.info(
        f"[knowledge] study queued: id={study_id} root={study_root} "
        f"framework={payload.framework} docs_url={payload.docs_url} "
        f"tier={payload.tier} github_discover={payload.github_discover}"
    )

    return {
        "study_id": study_id,
        "task_id": study_id,
        "study_root": study_root,
        "status": "queued",
        "endpoint": f"/api/v1/tasks/{study_id}",
        "stream_endpoint": f"/api/v1/knowledge/studies/{study_id}/stream",
        "detected_topic": scope.detected_topic,
        "language": scope.language,
        "docs_url": payload.docs_url,
    }


# =============================================================================
# GET /studies/{study_id} — status + phase + Celery meta
# =============================================================================
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
    include_raw: bool,
    include_exports: bool) -> tuple[io.BytesIO, str]:
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
        if rel.startswith("exports/") and not include_exports:
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
    include_raw: bool = True,
    include_exports: bool = False):
    """
    Download a study's artifacts by MinIO folder name.

    `{slug}` is the folder name under `{user_id}/knowledge/` — typically
    `<framework>-<timestamp>` (e.g. `pydantic-20260420T171547Z`) or
    `<framework>-<version>-<timestamp>`. You can list existing studies via
    `mc ls coelhonexus/{user_id}/knowledge/` or your MinIO console.

    Defaults: includes the full raw corpus under `research/raw/` so the
    downloaded bundle is self-contained and verifiable. Exports (PDF/HTML/
    EPUB/APKG) are excluded by default — fetch them separately when needed.

    Filters (query params):
      - `include_raw=false`   → skip `research/raw/*.md` (usually 500+ files, much smaller tarball)
      - `include_exports=true` → also include `exports/*` (PDF/HTML/EPUB/APKG)

    Usage:
      curl -o study.tar.gz \\
        "http://<host>/api/v1/knowledge/downloads/smoketest/pydantic-20260420T171547Z"
      tar -xzf study.tar.gz -C ~/Workbench/STUDIES/Pydantic/
    """
    storage = request.app.state.study_storage
    study_root = f"{user_id}/knowledge/{slug}"
    buf, filename = await _build_study_tarball(
        storage, study_root, slug, include_raw, include_exports,
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
# POST /studies/{study_id}/export — enqueue a derived-artifact render
# =============================================================================
@router.post("/studies/{study_id}/export")
async def export_study(
    study_id: str,
    payload: ExportRequest,
    request: Request):
    """
    Generate a derived artifact (PDF/HTML/EPUB/Anki) from the study's
    canonical markdown. Runs as a Celery task — the pandoc+xelatex render
    can take 30-60s for a full study, too long for a request round trip.

    Output lands at:
        <study_root>/exports/study.{pdf,html,epub,apkg}

    Returns:
        {
            "task_id": <celery id>,
            "status": "queued",
            "endpoint": "/api/v1/tasks/{task_id}",
            "study_id": ...,
            "format": ...,
            "expected_object_key": <minio key once rendered>,
        }

    Error codes:
        404: study_id not found in the registry.
    """
    record = await _load_study_record(request.app.state.redis_aio, study_id)
    if not record:
        raise HTTPException(status_code = 404, detail = "Study not found")
    from tasks.knowledge.export import export_study as export_task
    task = export_task.delay(
        study_id,
        record["study_root"],
        record["framework"],
        payload.format,
    )
    ext = "apkg" if payload.format == "anki" else payload.format
    expected_key = f"{record['study_root']}/exports/study.{ext}"
    logger.info(
        f"[knowledge] export queued: study={study_id} format={payload.format} "
        f"task_id={task.id} expected={expected_key}"
    )
    return {
        "task_id": task.id,
        "status": "queued",
        "endpoint": f"/api/v1/tasks/{task.id}",
        "study_id": study_id,
        "format": payload.format,
        "expected_object_key": expected_key,
    }


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
