"""Synth pipeline endpoints — per-chapter LangGraph runs.

Endpoint contract mirrors planner.py (so the FastHTML UI can use the
same SSE / poll / resume / cancel flows). Synth runs PER CHAPTER:
each chapter is its own thread_id + its own graph invocation.

Endpoints:

  GET  /synth/info
      → {node_order, implemented, modes}
  GET  /synth/recent
      → most-recent thread per slug for page-refresh recovery
        (chapter_id lives in state, NOT in the thread_id)
  POST /synth/{slug}?chapter_id=ch-..&mode=quality&thread_id=...
      → kick off a synth run for one chapter; returns thread_id +
        chapter_id. If chapter_id omitted, picks first chapter from
        `planner/{slug}/plan-latest.json`.
  POST /synth/{thread_id:path}/resume
      → resume from last checkpoint (LangGraph ainvoke(None))
  POST /synth/{thread_id:path}/cancel
      → cooperative cancel via Redis flag + asyncio.Task.cancel
  GET  /synth/{thread_id:path}/events
      → SSE stream of substep progress events
  GET  /synth/debug/graph/{thread_id:path}/state
      → latest LangGraph checkpoint values for the thread
  GET  /synth/debug/graph/{thread_id:path}/history
      → all checkpoints for the thread (debug)
  DELETE /synth/{slug}/wipe
      → delete MinIO synth/{slug}/ + Postgres checkpoints for the slug

thread_id format: `docs-distiller/synth/{slug}/{uuid}`
  (chapter_id is in SynthState, not in thread_id — see _make_thread_id)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from urllib.parse import quote

import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException, Query, Response
from starlette.responses import StreamingResponse

from domains.dd.ingestion.storage import get_storage
from domains.llm.rotator.discovery import missing_required_keys
from domains.dd.synth.cancel import (
    _redis_url,
    clear_cancel,
    request_cancel,
)
from domains.dd.synth.graph import (
    IMPLEMENTED,
    NODE_ORDER,
    NODE_REGISTRY,
    NODE_TO_FIELD,
    build_graph,
)
from domains.dd.synth.progress import (
    emit_progress,
    subscribe_progress,
)
# Bundle 13 (2026-05-26) — synth now runs on Celery (queue synth-{env}).
# These are the task handles we `.delay(...)` from the route handlers; the
# async runners they wrap live in domains/dd/synth/dispatch.py.
from domains.dd.synth.task import (
    resume_synth as resume_synth_task,
    run_single_chapter as run_single_chapter_task,
    run_study as run_study_task,
)
from domains.dd.synth.dispatch import _STUDY_SEM


logger = logging.getLogger(__name__)


router = APIRouter()


# =============================================================================
# Info + recent
# =============================================================================
@router.get("/info")
async def synth_info() -> dict:
    """Catalog of synth substeps + which are wired. UI uses this to mark
    cards as "future" vs "ready"."""
    return {
        "node_order":  list(NODE_ORDER),
        "implemented": list(IMPLEMENTED),
        "modes": [
            {"key": "quality", "label": "Quality (default)", "enabled": True},
            {"key": "fast",    "label": "Fast (3 iters)",    "enabled": False},
        ],
        "status": "live" if IMPLEMENTED else "scaffolding",
    }


# =============================================================================
# Step 5 Study viewer — artifact serving
# =============================================================================
# Per `docs/UI-ARCHITECTURE-SOTA-2026-05-18.md` 5-step pipeline:
#   Catalog → Ingestion → Planner → Synth → Study
# Step 5 (Study) needs to read the 3 artifacts that render_audit_write
# produces per chapter:
#   - synth/{slug}/{chapter_id}/README.md       (full chapter markdown)
#   - synth/{slug}/{chapter_id}/challenges.md   (active-recall questions)
#   - synth/{slug}/{chapter_id}/flashcards.json (Q/A pairs)
# Plus a "list chapters with their render-status" endpoint so the
# sidebar can show which chapters are ready vs not-yet-synthesized.

_VALID_ARTIFACTS = {
    "README.md":       "text/markdown; charset=utf-8",
    "challenges.md":   "text/markdown; charset=utf-8",
    "flashcards.json": "application/json",
}


@router.get("/{slug}/study/chapters")
async def list_study_chapters(slug: str, response: Response) -> dict:
    """Chapter list for the Step 5 study viewer. For each chapter in
    `planner/{slug}/plan-latest.json`, returns whether render_audit_write
    has produced its artifacts yet — drives the sidebar status badges.

    Shape:
      {
        "framework_slug": str,
        "chapters": [
          {
            "id":           "ch-01-introduction-to-pydantic-basics",
            "title":        "Introduction to Pydantic Basics",
            "order":        1,
            "n_sources":    9,
            "rendered":     true,
            "audit_passed": true,
            "render_path":  "synth/.../render-latest.json"  (when rendered)
          },
          ...
        ]
      }
    """
    # Never let the browser serve a stale render-status snapshot — these
    # `rendered` flags drive the Study sidebar + auto-open, and a cached
    # 200 from before a wipe would show phantom "synthesized" chapters.
    response.headers["Cache-Control"] = "no-store"
    plan = await _load_plan(slug)
    chapters_in: list[dict] = plan.get("chapters") or []
    if not chapters_in:
        return {"framework_slug": slug, "chapters": []}

    minio = get_storage()

    # Persisted timing roll-up (per-chapter wall + study total) so the
    # sidebar + navbar show times after a refresh / for cached studies.
    per_chapter_ms: dict = {}
    study_total_wall_ms = 0
    try:
        _t = json.loads(
            await minio.read_text(f"synth/{slug}/study-timing-latest.json")
        )
        per_chapter_ms = _t.get("per_chapter_ms") or {}
        study_total_wall_ms = int(_t.get("total_wall_ms") or 0)
    except Exception:
        pass

    out: list[dict] = []
    for ch in chapters_in:
        cid = (ch or {}).get("id")
        if not cid:
            continue
        render_key = (
            f"synth/{slug}/{cid}/render-latest.json"
        )
        rendered = await minio.exists(render_key)
        entry: dict = {
            "id":         cid,
            "title":      ch.get("title") or cid,
            "order":      ch.get("order") or 0,
            "n_sources":  len(ch.get("sources") or []),
            "rendered":   rendered,
            "audit_passed": False,
            "render_path": render_key if rendered else None,
            "wall_ms":    int(per_chapter_ms.get(cid, 0) or 0),
        }
        if rendered:
            try:
                text = await minio.read_text(render_key)
                rp = json.loads(text)
                entry["audit_passed"] = bool(
                    (rp.get("audit") or {}).get("audit_passed", False)
                )
                entry["rendered_chars"] = rp.get("rendered_chars", 0)
                entry["n_sections"] = rp.get("n_sections", 0)
                # Synth thread that produced this render — lets the UI
                # re-open the chapter's LangGraph canvas after a refresh.
                # May be absent on blobs written before this field shipped.
                entry["thread_id"] = rp.get("thread_id") or None
            except Exception:
                # render-latest exists but unparseable — flag as rendered
                # but audit unknown
                pass
        out.append(entry)
    return {
        "framework_slug": slug,
        "chapters": out,
        "study_total_wall_ms": study_total_wall_ms,
    }


@router.get("/{slug}/active")
async def synth_active(slug: str, response: Response) -> dict:
    """Is a STUDY synth run currently live for `slug`?

    Returns the study orchestrator's thread_id when one is registered (see
    start_synth → `dd:study:current:{slug}`), so a page refresh can
    reconnect to its SSE and restore the running-chapter highlight + live
    graph WITHOUT relying on browser localStorage. Cleared on terminal/wipe.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        r = redis_aio.from_url(
            _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            sid = await r.get(f"dd:study:current:{slug}")
        finally:
            await r.aclose()
    except Exception:
        return {"active": False}
    if not sid:
        return {"active": False}
    if isinstance(sid, (bytes, bytearray)):
        sid = sid.decode("utf-8", "replace")
    # New form: JSON {study_thread_id, started_ts}. Legacy form: plain
    # thread_id string. Tolerate both so an in-flight run started before this
    # change still reconnects.
    try:
        data = json.loads(sid)
        return {
            "active": True,
            "study_thread_id": data.get("study_thread_id"),
            "started_ts": data.get("started_ts"),
        }
    except Exception:
        return {"active": True, "study_thread_id": str(sid), "started_ts": None}


@router.get("/{slug}/study/{chapter_id}/artifact/{artifact_name}")
async def get_study_artifact(
    slug: str, chapter_id: str, artifact_name: str,
) -> StreamingResponse:
    """Stream one of the 3 chapter artifacts back to the browser. Used
    by the Step 5 viewer to render README.md (via marked.js), display
    challenges.md (also via marked.js), and parse flashcards.json
    client-side.

    Names enforced via `_VALID_ARTIFACTS` allow-list so this endpoint
    can't be used to read arbitrary MinIO keys.
    """
    if artifact_name not in _VALID_ARTIFACTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid artifact name {artifact_name!r}; valid: "
                f"{sorted(_VALID_ARTIFACTS)}"
            ),
        )
    key = f"synth/{slug}/{chapter_id}/{artifact_name}"
    minio = get_storage()
    if not await minio.exists(key):
        raise HTTPException(
            status_code=404,
            detail=(
                f"artifact {artifact_name!r} for chapter {chapter_id!r} "
                f"not in MinIO at {key!r}; run synth + render first"
            ),
        )

    async def _gen():
        try:
            text = await minio.read_text(key)
            yield text.encode("utf-8")
        except Exception as e:
            logger.warning(
                f"[synth-study-artifact] read failed for {key!r}: "
                f"{type(e).__name__}: {e}"
            )
            yield b""

    return StreamingResponse(
        _gen(),
        media_type=_VALID_ARTIFACTS[artifact_name],
        headers={
            "Cache-Control": "public, max-age=60",
        },
    )


@router.get("/recent")
async def list_recent_synth() -> dict:
    """Most-recent thread per slug. thread_id format:
    `docs-distiller/synth/{slug}/{uuid}` → split_part(thread_id, '/', 3)
    gives slug. chapter_id lives in state, NOT in the thread_id — see
    _make_thread_id rationale."""
    import psycopg

    pw = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe="")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get(
        "POSTGRES_DATABASE", os.environ.get("POSTGRES_DB", "postgres"),
    )
    user = os.environ.get("POSTGRES_USER", "postgres")
    dsn = f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    out: list[dict] = []
    try:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    WITH thread_stats AS (
                        SELECT
                            split_part(thread_id, '/', 3) AS slug,
                            thread_id,
                            count(*)           AS ckpt_count,
                            max(checkpoint_id) AS latest_ckpt
                        FROM checkpoints
                        WHERE thread_id LIKE 'docs-distiller/synth/%'
                        GROUP BY thread_id
                    )
                    SELECT DISTINCT ON (slug)
                        slug, thread_id, ckpt_count, latest_ckpt
                    FROM thread_stats
                    ORDER BY slug, ckpt_count DESC, latest_ckpt DESC
                """)
                for slug, tid, ckpt_count, latest in await cur.fetchall():
                    out.append({
                        "slug":          slug,
                        "thread_id":     tid,
                        "checkpoint_id": str(latest),
                        "ckpt_count":    int(ckpt_count),
                    })
    except Exception as e:
        logger.warning(f"[synth-recent] query failed: {e}")
    return {"recent": out}


# =============================================================================
# Background runners + study orchestrator moved to domains/dd/synth/dispatch.py
# (Bundle 13, 2026-05-26). They now run inside Celery workers on queue
# synth-{env}; the FastAPI routes only enqueue via .delay().
# =============================================================================


# =============================================================================
# Helpers
# =============================================================================
def _planner_latest_key(slug: str) -> str:
    return f"planner/{slug}/plan-latest.json"


async def _load_plan(slug: str) -> dict:
    minio = get_storage()
    plan_key = _planner_latest_key(slug)
    if not await minio.exists(plan_key):
        raise HTTPException(
            status_code=404,
            detail=(
                f"no planner plan for {slug!r} — run the planner first "
                f"(POST /planner/{slug})"
            ),
        )
    try:
        text = await minio.read_text(plan_key)
        return json.loads(text) or {}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"plan {plan_key!r} unreadable: {type(e).__name__}: {e}",
        )


def _pick_first_chapter_id(plan: dict) -> str | None:
    chapters = plan.get("chapters") or []
    for ch in chapters:
        cid = (ch or {}).get("id")
        if cid:
            return cid
    return None


_VALID_MODES = {"quality", "fast"}

# Server-side single-flight lock for synth runs. One synth (study or
# single-chapter) of any slug at a time across the whole deployment
# (mirrors POST /runs ingestion). TTL matches the study orchestrator
# Celery `time_limit` hard ceiling — long enough to cover the worst-case
# study, short enough to leak-insure a crashed worker.
_SYNTH_LOCK_TTL_S = 21900


def _make_thread_id(slug: str, chapter_id: str | None = None) -> str:
    """Canonical per-chapter synth thread_id. MUST match
    apps/fasthtml/static/js/docs_distiller.js:_genSynthThreadId so the
    Redis channel + /synth/recent SQL + /synth/{slug}/wipe SQL pattern-
    match correctly across client/server.

    `chapter_id` is intentionally NOT embedded in the thread_id: the JS
    pre-generates a thread_id for the Cancel button BEFORE the POST
    response (no "pending" dead-zone), and at that point it doesn't
    know which chapter the server will pick. The chapter_id lives in
    SynthState instead — recoverable via /debug/graph/{tid}/state."""
    return f"docs-distiller/synth/{slug}/{uuid.uuid4()}"


def _make_study_thread_id(slug: str) -> str:
    """Study orchestrator thread_id — distinct prefix so /synth/recent
    + /synth/{slug}/wipe SQL pattern-matchers can tell apart per-chapter
    runs from study-level orchestrator runs. Used as the SSE channel
    the UI subscribes to for orchestrator-level events (study_start,
    chapter_running, chapter_done, study_done)."""
    return f"docs-distiller/study/{slug}/{uuid.uuid4()}"



# =============================================================================
# Start synth — two modes:
#   - POST /synth/{slug}                    → STUDY mode: orchestrator runs
#                                              ALL chapters sequentially
#   - POST /synth/{slug}?chapter_id=X       → SINGLE mode: just chapter X
# =============================================================================
@router.post("/{slug}")
async def start_synth(
    slug: str,
    chapter_id: str | None = Query(default=None),
    mode: str = Query(default="quality"),
    thread_id: str | None = Query(default=None),
) -> dict:
    """Kick off a synth run.

    Behavior depends on whether `chapter_id` is provided:

      - With `chapter_id`: single-chapter run (escape hatch for re-running
        one specific chapter; preserved for the existing UI single-chapter
        flow). Returns `{thread_id, chapter_id, status: "running"}`.

      - Without `chapter_id`: STUDY mode — spawns the orchestrator
        background task that runs ALL chapters in `plan-latest.json`
        sequentially (sem=1 for v1; raise to 2 if free-tier rotator
        can handle the burst). Returns `{study_thread_id, n_chapters,
        chapter_ids, status: "running"}`. The UI subscribes to the
        study thread for orchestrator events and opens per-chapter
        SSE connections on `chapter_running` events.
    """
    if mode not in _VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode {mode!r}; expected one of {sorted(_VALID_MODES)}",
        )

    # NIM-REQUIRED GATE — synth reranks/grounds via NVIDIA NIM (faithfulness +
    # CoCoA). Fail fast with an actionable message if the NIM key isn't set.
    _missing = missing_required_keys()
    if _missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "NVIDIA NIM API key required — it powers the mandatory embedding "
                "+ reranking models this run needs. Add "
                + ", ".join(m["key_env"] for m in _missing)
                + " in Settings (/settings), then retry."
            ),
        )

    # PLANNER-FIRST GATE (server-side anti-bypass). Synth cannot run
    # without a planner plan. _load_plan raises 404 ("run the planner
    # first") when planner/{slug}/plan-latest.json is absent, so a direct
    # POST that skips the disabled Start Synth button is still rejected.
    # This is the single synth entry point — STUDY mode and single-chapter
    # mode below both depend on the plan loaded here; /resume only acts on
    # an already-checkpointed thread, so there is no bypass path.
    plan = await _load_plan(slug)
    plan_chapter_ids: list[str] = sorted(
        c["id"] for c in (plan.get("chapters") or [])
        if (c or {}).get("id")
    )
    if not plan_chapter_ids:
        raise HTTPException(
            status_code=404,
            detail=f"plan for {slug!r} has no chapters",
        )

    # ── STUDY MODE — orchestrator over all chapters ────────────────────
    if chapter_id is None:
        study_thread_id = thread_id or _make_study_thread_id(slug)

        # Server-side single-flight gate (mirrors POST /runs). Three phases:
        #   0.  CROSS-STAGE — Planner and Synth must NOT run simultaneously
        #       (LLM-resource contention degrades both pipelines).
        #   1a. GLOBAL same-stage — any OTHER slug's synth running?
        #   1b. SAME-SLUG — atomic SET NX on dd:synth:lock:{slug}.
        # Every `locked` response carries a `stage` field so the FastHTML
        # caller can render the appropriate "running on X" affordance.
        r = redis_aio.from_url(
            _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            # 0. CROSS-STAGE — is any PLANNER currently running anywhere?
            cursor = 0
            while True:
                cursor, keys = await r.scan(
                    cursor=cursor, match="dd:planner:lock:*", count=100,
                )
                for k in keys:
                    ks = k.decode() if isinstance(k, bytes) else k
                    planner_slug = ks.split("dd:planner:lock:", 1)[-1]
                    val = await r.get(ks)
                    if val is None:
                        continue
                    planner_thread = (
                        val.decode() if isinstance(val, bytes) else val
                    )
                    return {
                        "status": "locked",
                        "slug": planner_slug,
                        "thread_id": planner_thread,
                        "stage": "planner",
                        "message": (
                            f"A planner is running ({planner_slug!r}, "
                            f"thread_id={planner_thread}). Planner and "
                            f"Synth share the same LLM resources — "
                            f"running both at once degrades quality on "
                            f"each. Wait for the planner to finish or "
                            f"cancel it before starting a synth."
                        ),
                    }
                if cursor == 0:
                    break

            # 1a. GLOBAL — any OTHER slug's synth currently running?
            cursor = 0
            while True:
                cursor, keys = await r.scan(
                    cursor=cursor, match="dd:synth:lock:*", count=100,
                )
                for k in keys:
                    ks = k.decode() if isinstance(k, bytes) else k
                    other_slug = ks.split("dd:synth:lock:", 1)[-1]
                    if other_slug == slug:
                        continue   # same-slug handled by SET NX below
                    val = await r.get(ks)
                    if val is None:
                        continue
                    other_thread = (
                        val.decode() if isinstance(val, bytes) else val
                    )
                    return {
                        "status": "locked",
                        "slug": other_slug,
                        "thread_id": other_thread,
                        "stage": "synth",
                        "message": (
                            f"Another synth is running ({other_slug!r}, "
                            f"thread_id={other_thread}). Wait for it to "
                            f"finish or cancel it before starting {slug!r}."
                        ),
                    }
                if cursor == 0:
                    break

            # 1b. SAME-SLUG — atomic SET NX. Failure → another synth of
            # this slug is already running (study OR single-chapter).
            acquired = await r.set(
                f"dd:synth:lock:{slug}", study_thread_id,
                nx=True, ex=_SYNTH_LOCK_TTL_S,
            )
            if not acquired:
                existing = await r.get(f"dd:synth:lock:{slug}")
                existing_tid = (
                    existing.decode() if isinstance(existing, bytes)
                    else existing
                ) if existing else None
                return {
                    "status": "locked",
                    "slug": slug,
                    "thread_id": existing_tid,
                    "stage": "synth",
                    "message": (
                        f"A synth of {slug!r} is already running "
                        f"(thread_id={existing_tid}). Wait for it to "
                        f"finish or cancel it before retrying."
                    ),
                }

            await clear_cancel(r, study_thread_id)

            # Bundle 13 — dispatch via Celery (queue synth-{env}). The
            # route returns immediately with `status="queued"`; the worker
            # emits progress events on Redis pub/sub that this FastAPI's
            # SSE endpoints stream to the UI.
            try:
                async_result = run_study_task.delay(
                    study_thread_id, slug, plan_chapter_ids, mode,
                )
            except Exception as e:
                try:
                    await r.delete(f"dd:synth:lock:{slug}")
                except Exception:
                    pass
                logger.exception(
                    f"[synth-study] {study_thread_id}: celery dispatch "
                    f"failed: {type(e).__name__}: {e}"
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"celery dispatch failed: "
                        f"{type(e).__name__}: {e}"
                    ),
                )
        finally:
            await r.aclose()

        # Register the live study run so a page refresh — even with cleared
        # browser storage or in another tab — can reconnect to its SSE and
        # restore the running-chapter blue highlight + live graph. Cleared on
        # study terminal (orchestrator) and on Wipe Synth.
        try:
            r2 = redis_aio.from_url(
                _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
            )
            try:
                # JSON value carries started_ts so a refresh can SEED the
                # navbar timer from the real run start (continuous), not 0.
                # study_start's own ts can age out of the 200-event SSE
                # snapshot on a long book, so the registry is the durable
                # source. synth_active tolerates the legacy plain-string form.
                await r2.set(
                    f"dd:study:current:{slug}",
                    json.dumps({
                        "study_thread_id": study_thread_id,
                        "started_ts": time.time(),
                    }),
                    ex=14400,
                )
            finally:
                await r2.aclose()
        except Exception as e:
            logger.warning(
                f"[synth-study] {slug}: active-run register failed: "
                f"{type(e).__name__}: {e}"
            )

        return {
            "study_thread_id": study_thread_id,
            "slug":            slug,
            "n_chapters":      len(plan_chapter_ids),
            "chapter_ids":     plan_chapter_ids,
            "mode":            mode,
            "concurrency":     _STUDY_SEM,
            "status":          "queued",
            "celery_task_id":  async_result.id,
            "latency_ms":      0,
        }

    # ── SINGLE-CHAPTER MODE — preserved escape hatch ───────────────────
    if chapter_id not in set(plan_chapter_ids):
        raise HTTPException(
            status_code=404,
            detail=(
                f"chapter {chapter_id!r} not in plan; known ids: "
                f"{plan_chapter_ids}"
            ),
        )

    if not thread_id:
        thread_id = _make_thread_id(slug, chapter_id)

    # Server-side single-flight gate — same shape as STUDY mode above
    # (same `dd:synth:lock:{slug}` namespace, so a single-chapter run is
    # blocked while a study orchestrator is running for the same slug,
    # and vice versa). Same three phases: cross-stage / global / per-slug.
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        # 0. CROSS-STAGE — is any PLANNER currently running anywhere?
        cursor = 0
        while True:
            cursor, keys = await r.scan(
                cursor=cursor, match="dd:planner:lock:*", count=100,
            )
            for k in keys:
                ks = k.decode() if isinstance(k, bytes) else k
                planner_slug = ks.split("dd:planner:lock:", 1)[-1]
                val = await r.get(ks)
                if val is None:
                    continue
                planner_thread = (
                    val.decode() if isinstance(val, bytes) else val
                )
                return {
                    "status": "locked",
                    "slug": planner_slug,
                    "thread_id": planner_thread,
                    "stage": "planner",
                    "message": (
                        f"A planner is running ({planner_slug!r}, "
                        f"thread_id={planner_thread}). Planner and "
                        f"Synth share the same LLM resources — running "
                        f"both at once degrades quality on each. Wait "
                        f"for the planner to finish or cancel it before "
                        f"starting a synth."
                    ),
                }
            if cursor == 0:
                break

        # 1a. GLOBAL — any OTHER slug's synth currently running?
        cursor = 0
        while True:
            cursor, keys = await r.scan(
                cursor=cursor, match="dd:synth:lock:*", count=100,
            )
            for k in keys:
                ks = k.decode() if isinstance(k, bytes) else k
                other_slug = ks.split("dd:synth:lock:", 1)[-1]
                if other_slug == slug:
                    continue
                val = await r.get(ks)
                if val is None:
                    continue
                other_thread = (
                    val.decode() if isinstance(val, bytes) else val
                )
                return {
                    "status": "locked",
                    "slug": other_slug,
                    "thread_id": other_thread,
                    "stage": "synth",
                    "message": (
                        f"Another synth is running ({other_slug!r}, "
                        f"thread_id={other_thread}). Wait for it to "
                        f"finish or cancel it before starting {slug!r}."
                    ),
                }
            if cursor == 0:
                break

        # 1b. SAME-SLUG — atomic SET NX.
        acquired = await r.set(
            f"dd:synth:lock:{slug}", thread_id,
            nx=True, ex=_SYNTH_LOCK_TTL_S,
        )
        if not acquired:
            existing = await r.get(f"dd:synth:lock:{slug}")
            existing_tid = (
                existing.decode() if isinstance(existing, bytes)
                else existing
            ) if existing else None
            return {
                "status": "locked",
                "slug": slug,
                "thread_id": existing_tid,
                "stage": "synth",
                "message": (
                    f"A synth of {slug!r} is already running "
                    f"(thread_id={existing_tid}). Wait for it to finish "
                    f"or cancel it before retrying."
                ),
            }

        await clear_cancel(r, thread_id)

        # Bundle 13 — single-chapter run on Celery (queue synth-{env}).
        try:
            async_result = run_single_chapter_task.delay(
                thread_id, slug, chapter_id, mode,
            )
        except Exception as e:
            try:
                await r.delete(f"dd:synth:lock:{slug}")
            except Exception:
                pass
            logger.exception(
                f"[synth] {thread_id}: celery dispatch failed: "
                f"{type(e).__name__}: {e}"
            )
            raise HTTPException(
                status_code=503,
                detail=f"celery dispatch failed: {type(e).__name__}: {e}",
            )
    finally:
        await r.aclose()

    return {
        "thread_id":      thread_id,
        "slug":           slug,
        "chapter_id":     chapter_id,
        "mode":           mode,
        "status":         "queued",
        "celery_task_id": async_result.id,
        "latency_ms":     0,
    }


# =============================================================================
# Resume
# =============================================================================
# Bundle 13 — the resume catch-up helpers moved to
# domains/dd/synth/dispatch.py (missing_implemented_nodes,
# run_missing_nodes_async). Both are invoked from the Celery task now.


@router.post("/{thread_id:path}/resume")
async def resume_synth(thread_id: str) -> dict:
    """Resume a synth run from its last checkpoint by dispatching the
    `resume_synth` Celery task (queue synth-{env}).

    All three sub-paths (standard resume / catch-up missing nodes /
    nothing to do) are handled inside `dispatch.resume_synth_async`.
    The route returns immediately with the celery_task_id; the SSE
    endpoint streams the worker's progress events to the UI.
    """
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await clear_cancel(r, thread_id)
    finally:
        await r.aclose()

    try:
        async_result = resume_synth_task.delay(thread_id)
    except Exception as e:
        logger.exception(
            f"[synth] {thread_id}: celery resume dispatch failed: "
            f"{type(e).__name__}: {e}"
        )
        raise HTTPException(
            status_code=503,
            detail=f"celery dispatch failed: {type(e).__name__}: {e}",
        )

    return {
        "thread_id":      thread_id,
        "status":         "queued",
        "celery_task_id": async_result.id,
    }


# =============================================================================
# Cancel
# =============================================================================
@router.post("/{thread_id:path}/cancel")
async def cancel_synth(thread_id: str) -> dict:
    """Set the cancel flag — the watcher polling alongside the synth task
    picks it up within ~1s and cancels the main task.

    BUG FIX 2026-05-24: when `thread_id` is a STUDY thread
    (`docs-distiller/study/{slug}/{uuid}`), the study-level flag alone
    is not enough — in-flight per-chapter tasks have their own watchers
    polling their own per-chapter flags. Without propagation, cancelling
    a study only blocks the NEXT chapter from starting; the in-flight
    chapter continues to completion (LLM calls keep firing, the user
    sees the button stuck on "Cancelling…").

    Fix: when path indicates a study thread, scan Redis for active synth
    chapter threads with the matching slug and propagate the cancel flag
    to each. The chapter watchers pick up within ~1s and cancel cleanly.
    """
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    propagated_to: list[str] = []
    try:
        await request_cancel(r, thread_id)
        # Detect study thread + propagate to all active chapter threads
        # for the same slug. Pattern is `docs-distiller/study/{slug}/{uuid}`.
        parts = thread_id.split("/")
        if len(parts) >= 4 and parts[1] == "study":
            slug = parts[2]
            chapter_prefix = f"docs-distiller/synth/{slug}/"
            # Active chapter threads are those with a recent events
            # snapshot key (only created when emit_progress fires) AND
            # without a terminal event yet. The scan is bounded by the
            # ~hundreds-of-keys typical fastmcp/langchain study, so it's
            # cheap; for larger corpora replace with an explicit per-
            # study active-chapter set written by the orchestrator.
            scan_pattern = (
                f"dd:synth:{chapter_prefix}*:events:snapshot"
            )
            try:
                async for key in r.scan_iter(match=scan_pattern, count=200):
                    if isinstance(key, bytes):
                        key = key.decode()
                    # Extract per-chapter thread_id from the key shape:
                    # `dd:synth:{thread_id}:events:snapshot`
                    ch_tid = key[len("dd:synth:"):-len(":events:snapshot")]
                    await request_cancel(r, ch_tid)
                    propagated_to.append(ch_tid)
            except Exception as e:
                logger.warning(
                    f"[cancel_synth] scan/propagate failed for {thread_id!r}: "
                    f"{type(e).__name__}: {e}"
                )
    finally:
        await r.aclose()

    await emit_progress(thread_id, "synth", "cancel_requested")
    # Also emit on every propagated chapter channel so the UI SSE
    # listener can react immediately.
    for ch_tid in propagated_to:
        try:
            await emit_progress(ch_tid, "synth", "cancel_requested")
        except Exception:
            pass
    logger.info(
        f"[cancel_synth] {thread_id}: flag set; "
        f"propagated to {len(propagated_to)} chapter thread(s)"
    )
    return {
        "thread_id":     thread_id,
        "status":        "cancel_requested",
        "propagated_to": propagated_to,
    }


# =============================================================================
# SSE events
# =============================================================================
@router.get("/{thread_id:path}/events")
async def synth_events(thread_id: str) -> StreamingResponse:
    """SSE stream of substep progress events for `thread_id`.

    Mirrors the planner's events endpoint EXACTLY — that's deliberate.
    Two subtleties make real-time delivery work behind nginx-class
    proxies:

      1. Initial comment-line (`: stream open`) flushed BEFORE any
         Redis event arrives — forces the proxy to send headers
         downstream + stop buffering. Without this, the proxy holds
         the response until it accumulates enough bytes, which can
         delay the first event by tens of seconds (or until the
         stream closes — appearing as "no real-time updates").
      2. `X-Accel-Buffering: no` + `Cache-Control: no-cache, no-transform`
         + `Connection: keep-alive` — the canonical SSE-friendly
         header trio that prevents per-byte buffering downstream.

    The stream stays open until the client closes the EventSource.
    The server does NOT terminate on `kind=='terminal'` — the JS
    closes its end after handling the terminal event so it can flush
    the very last paint."""

    # 2026-05-26 — Heartbeat every _SSE_HEARTBEAT_S to keep idle
    # connections alive through k3d/traefik/nginx-class proxies (default
    # idle-stream timeout 60s). During long Synth nodes (sawc_write LLM
    # calls, book_harmonize) Redis pub/sub can have 30s-15min event gaps;
    # without this, the proxy kills the TCP connection and the browser
    # loses every event emitted in the down-window. SSE comments (`:`
    # prefix) are ignored by clients but their bytes flow through TCP.
    _SSE_HEARTBEAT_S = 15.0
    _DONE = object()

    async def _gen():
        yield b": stream open\n\n"
        queue: asyncio.Queue = asyncio.Queue()

        async def _pump():
            try:
                async for event in subscribe_progress(thread_id):
                    await queue.put(event)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(
                    f"[synth-events] {thread_id}: pump crashed "
                    f"({type(e).__name__}: {e})"
                )
            finally:
                await queue.put(_DONE)

        pump_task = asyncio.create_task(_pump())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_SSE_HEARTBEAT_S,
                    )
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if event is _DONE:
                    return
                try:
                    payload = json.dumps(event, default=str)
                except Exception:
                    continue
                yield f"data: {payload}\n\n".encode("utf-8")
        except asyncio.CancelledError:
            return
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache, no-transform",
            "Connection":        "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# Debug
# =============================================================================
@router.get("/debug/graph/{thread_id:path}/state")
async def synth_state(thread_id: str) -> dict:
    """Latest LangGraph checkpoint values for the thread."""
    try:
        graph = build_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    config = {"configurable": {"thread_id": thread_id}}
    snap = await graph.aget_state(config)
    if snap.values == {}:
        raise HTTPException(
            status_code=404,
            detail=f"no checkpoints for thread_id={thread_id!r}",
        )
    return {
        "thread_id":   thread_id,
        "values":      dict(snap.values or {}),
        "next":        list(snap.next or []),
        "config":      snap.config,
        "metadata":    snap.metadata,
        "created_at":  str(snap.created_at) if snap.created_at else None,
    }


@router.get("/debug/graph/{thread_id:path}/history")
async def synth_history(thread_id: str) -> dict:
    """All checkpoints (super-steps) for the thread, newest first."""
    try:
        graph = build_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    config = {"configurable": {"thread_id": thread_id}}
    history: list[dict] = []
    async for snap in graph.aget_state_history(config):
        history.append({
            "checkpoint_id": (snap.config or {})
                .get("configurable", {})
                .get("checkpoint_id"),
            "next":          list(snap.next or []),
            "metadata":      snap.metadata,
            "created_at":    str(snap.created_at) if snap.created_at else None,
            "state_keys":    sorted((snap.values or {}).keys()),
        })
    return {"thread_id": thread_id, "history": history}


# =============================================================================
# Wipe
# =============================================================================
@router.delete("/{slug}/wipe")
async def wipe_synth(slug: str) -> dict:
    """Delete ALL synth state for `slug`: MinIO synth/{slug}/ blobs +
    Postgres LangGraph checkpoints for any thread under
    docs-distiller/synth/{slug}/."""
    import psycopg

    if not slug or "/" in slug:
        raise HTTPException(
            status_code=400,
            detail=f"invalid slug {slug!r}; slashes not allowed",
        )

    minio = get_storage()
    try:
        n_minio = await minio.delete_prefix(f"synth/{slug}/")
    except Exception as e:
        logger.warning(f"[synth-wipe] MinIO delete failed for {slug!r}: {e}")
        n_minio = -1

    pw = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe="")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get(
        "POSTGRES_DATABASE", os.environ.get("POSTGRES_DB", "postgres"),
    )
    user = os.environ.get("POSTGRES_USER", "postgres")
    dsn = f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    # Cover BOTH per-chapter synth threads AND the study-orchestrator
    # threads for this slug — they live under distinct thread_id prefixes
    # (docs-distiller/synth/{slug}/ vs docs-distiller/study/{slug}/).
    patterns = [
        f"docs-distiller/synth/{slug}/%",
        f"docs-distiller/study/{slug}/%",
    ]
    counts: dict = {}
    try:
        async with await psycopg.AsyncConnection.connect(
            dsn, autocommit=True,
        ) as conn:
            for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                async with conn.cursor() as cur:
                    try:
                        rows = 0
                        for pat in patterns:
                            await cur.execute(
                                f"DELETE FROM {tbl} WHERE thread_id LIKE %s",
                                (pat,),
                            )
                            rows += cur.rowcount
                        counts[tbl] = rows
                    except Exception as e:
                        counts[tbl] = f"skipped: {type(e).__name__}: {e}"
    except Exception as e:
        logger.warning(f"[synth-wipe] Postgres delete failed for {slug!r}: {e}")
        counts["error"] = f"{type(e).__name__}: {e}"

    # Redis cleanup — the SSE progress snapshots
    # (`dd:synth:{thread_id}:events:snapshot`, TTL 24h), pub/sub channel
    # keys, and cancel flags for every per-chapter AND study-orchestrator
    # thread of this slug. WITHOUT this, a wiped slug "comes back from the
    # dead": on the next page load `pollStudyState` opens the study
    # thread's SSE, which replays the cached snapshot (chapter_ready +
    # study_done events) and re-marks every chapter "Done" — the artifacts
    # and checkpoints are gone but the UI shows a fully-cached study that
    # survives even a hard refresh. Anchored patterns (.../synth/{slug}/
    # and .../study/{slug}/) avoid substring-slug collisions (e.g.
    # "claude-code" vs "claude-code-extra").
    n_redis = 0
    try:
        r = redis_aio.from_url(
            _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            for kind in ("synth", "study"):
                match = f"dd:synth:docs-distiller/{kind}/{slug}/*"
                batch: list = []
                async for k in r.scan_iter(match=match, count=500):
                    batch.append(k)
                    if len(batch) >= 500:
                        n_redis += await r.delete(*batch)
                        batch = []
                if batch:
                    n_redis += await r.delete(*batch)
            # Live-run registry (see start_synth /active) AND the
            # single-flight lock — drop both so a wiped slug isn't
            # rejected on the next Start Synth click by a stale lock
            # that survived the wipe.
            n_redis += await r.delete(
                f"dd:study:current:{slug}",
                f"dd:synth:lock:{slug}",
            )
        finally:
            await r.aclose()
    except Exception as e:
        logger.warning(f"[synth-wipe] Redis delete failed for {slug!r}: {e}")
        n_redis = -1

    logger.info(
        f"[synth-wipe] {slug}: minio={n_minio} blobs, postgres={counts}, "
        f"redis={n_redis} keys"
    )
    return {
        "slug":                  slug,
        "minio_blobs_deleted":   n_minio,
        "postgres_rows_deleted": counts,
        "redis_keys_deleted":    n_redis,
    }
