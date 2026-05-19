"""Synth pipeline endpoints — STUB router (UI scaffolding only).

Mirrors the Planner endpoint surface so the FastHTML Synth page (Step 4)
can scaffold start/cancel/wipe/resume/state/SSE flows end-to-end without
node code existing yet. Every endpoint either returns an empty/no-op
response (so the UI renders cleanly) or 503 "not implemented" (so a
button click surfaces a clear toast instead of a silent failure).

As individual synth nodes ship, this file becomes the real router by
swapping stubs for live implementations. The endpoint contract is the
SAME as planner.py — UI changes will be minimal.

Endpoint contract (per `docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`):

  GET  /synth/info
      → {node_order, implemented (empty until nodes land), modes}
        used by JS to mark cards as "future" vs "ready to run"
  GET  /synth/recent
      → {recent: []} — page-refresh recovery; empty until any run exists
  POST /synth/{slug}
      → kick off a synth run for `slug` (CURRENTLY: 503)
  POST /synth/{thread_id:path}/resume
      → resume from last checkpoint (CURRENTLY: 503)
  POST /synth/{thread_id:path}/cancel
      → cooperative cancel (CURRENTLY: 503)
  GET  /synth/{thread_id:path}/events
      → SSE stream of per-substep progress (CURRENTLY: immediately closes)
  GET  /synth/debug/graph/{thread_id:path}/state
      → current LangGraph checkpoint values (CURRENTLY: 404)
  DELETE /synth/{slug}/wipe
      → delete MinIO chapter artifacts + Postgres checkpoints (CURRENTLY: 503)
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from starlette.responses import StreamingResponse


logger = logging.getLogger(__name__)

router = APIRouter()


# Canonical node order per `docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`.
# Mirrors the planner's NODE_ORDER / IMPLEMENTED split — `implemented` is
# the subset of `node_order` that has real code wired.
#
# `cache_lookup` removed 2026-05-19 — see git history. Per-stage MinIO
# content-addressed caches + LangGraph's native skip-completed-nodes
# subsume it.
#
# `corpus_normalize` + `vault_sentinelize` removed 2026-05-19 — they
# ARE shipped, but execute at INGESTION-time (in store.py:add_page),
# NOT during synth. The synth canvas mental model is "what runs when
# the user clicks Start Synth"; ingestion-time preprocessors don't
# belong here. They live in `services/docs_distiller/synth/` as a
# library + ingestion-side hook; the synth graph reads their MinIO
# artifacts as inputs. See SYNTH-ARCHITECTURE-SOTA doc for the
# reclassification rationale.
NODE_ORDER = (
    "outline_sdp",
    "digest_construct",
    "sawc_write",
    "checklist_eval",
    "mgsr_replan",
    "render_audit_write",
)
IMPLEMENTED: tuple[str, ...] = ()


@router.get("/info")
async def synth_info() -> dict:
    """Catalog of synth substeps + which are wired into the runtime.
    Symmetric with /planner/info — the UI consumes the same shape."""
    return {
        "node_order":  list(NODE_ORDER),
        "implemented": list(IMPLEMENTED),
        "modes": [
            {"key": "quality", "label": "Quality (default)", "enabled": True},
            {"key": "fast",    "label": "Fast (3 iters)",    "enabled": True},
        ],
        "status": "scaffolding",
        "note":   (
            "Synth UI scaffolding is in place; node code ships "
            "incrementally per docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md."
        ),
    }


@router.get("/recent")
async def list_recent_synth() -> dict:
    """Most-recent thread per slug for page-refresh recovery. Empty until
    the first synth run completes."""
    return {"recent": []}


@router.post("/{slug}")
async def start_synth(
    slug: str, mode: str = "quality", thread_id: str | None = None,
) -> dict:
    """Kick off a synth run for `slug`. Currently 503 — node code lands
    incrementally; see GET /synth/info `implemented` for what's wired."""
    raise HTTPException(
        status_code=503,
        detail=(
            "Synth pipeline not yet implemented. UI scaffolding is in "
            "place — substeps will light up as each node ships. See "
            "`docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md` for the "
            "9-substep architecture and implementation order."
        ),
    )


@router.post("/{thread_id:path}/resume")
async def resume_synth(thread_id: str) -> dict:
    """Resume from last checkpoint. Currently 503."""
    raise HTTPException(
        status_code=503,
        detail="Synth resume not yet implemented.",
    )


@router.post("/{thread_id:path}/cancel")
async def cancel_synth(thread_id: str) -> dict:
    """Cooperative cancel. Currently no-op."""
    return {"thread_id": thread_id, "status": "noop",
            "note": "synth not implemented; nothing to cancel"}


async def _empty_sse_stream() -> AsyncIterator[bytes]:
    """One-shot SSE that emits a single `terminal` event then closes —
    UI's EventSource consumer treats this as "nothing to subscribe to,"
    handles the close cleanly, and doesn't reconnect-loop."""
    payload = json.dumps({
        "step":   "synth",
        "kind":   "terminal",
        "status": "not_implemented",
    })
    yield f"data: {payload}\n\n".encode("utf-8")
    # Tiny grace period so the browser flushes the event before close.
    await asyncio.sleep(0.05)


@router.get("/{thread_id:path}/events")
async def synth_events(thread_id: str) -> StreamingResponse:
    """SSE event stream for `thread_id`. Currently emits a single
    terminal/not_implemented event and closes — placeholder for the
    real Redis pub/sub subscriber bridged from `synth/progress.py`
    (when that ships)."""
    return StreamingResponse(
        _empty_sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/debug/graph/{thread_id:path}/state")
async def synth_state(thread_id: str) -> dict:
    """Current state values for the thread. Currently 404 — no synth
    threads exist (no graph compiled)."""
    raise HTTPException(
        status_code=404,
        detail=(
            "No synth checkpoints exist — pipeline not yet implemented."
        ),
    )


@router.delete("/{slug}/wipe")
async def wipe_synth(slug: str) -> dict:
    """Destructive: delete MinIO chapter artifacts + Postgres checkpoints
    + Redis caches for `slug`. Currently 503 — nothing to wipe."""
    raise HTTPException(
        status_code=503,
        detail="Synth wipe not yet implemented — nothing to clean up.",
    )
