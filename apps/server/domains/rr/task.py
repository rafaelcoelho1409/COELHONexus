"""Celery bridge for the Research Radar agent.

Queued from `POST /v1/rr/scan`; worker runs the full DeepAgents agent
end-to-end and persists the final digest. Phase progress streams over
Redis pub/sub for the SSE endpoint.

Same shape as `domains/dd/planner/task.py`: asyncio.run bridge + dict
return + try/except → status='failed' envelope. Phase events emitted
SYNCHRONOUSLY via `runtime.service.emit_event_sync` so they survive even when the
agent run is cancelled mid-await.

Thin bridge only — the pipeline lives in `service.py` (orchestration)
and `domain.py` (pure converters). Anything that is not an
`@infra.celery.service.app.task` belongs there, not here.
"""
from __future__ import annotations
import infra
import infra.celery
from . import runtime, service

import asyncio
import logging
from uuid import UUID


logger = logging.getLogger(__name__)


@infra.celery.service.app.task(
    name           = "domains.rr.task.run_radar_scan",
    bind           = True,
    acks_late      = False,
    track_started  = True,
    # 2026-09-17: soft_time_limit=1800/time_limit=2100 removed — same
    # bug DD's Planner hit and fixed on 2026-09-09 (see that task's
    # comment). Confirmed live: scan c01bf761 was killed by
    # SoftTimeLimitExceeded at exactly 1800.03s while STILL making real
    # progress (graph_build had just persisted 8/8 papers moments
    # before; synthesis was mid-retry-loop, not hung) — a prior scan on
    # the identical topic/top_n had already finished healthily in
    # 1557s, 87% of this same budget, so the margin was never safe
    # against normal NIM latency variance. Celery's signal-based
    # timeout can't distinguish "hung" from "genuinely slow"; RR
    # already has a real cooperative-cancel path for a truly-stuck scan
    # (`service.cancel_scan` → `celery_app.control.revoke(...,
    # terminate=True)`, wired to the UI's cancel button) — no blanket
    # wall-clock cap needed on top of it.
)
def run_radar_scan(
    self,
    scan_id: str,
    profile_id: str,
    topic: str,
    verticals: list[str] | None = None,
    top_n: int = 12,
) -> dict:
    """Run one Research Radar scan end-to-end.

    Args:
        scan_id:    UUID string from the API layer.
        profile_id: Interest-profile id (partitions radar_seen).
        topic:      Topical query (2-8 words) — fed into discovery
                    subagents' query field.
        verticals:  Vertical categories for signal_score.vertical_fit.
        top_n:      How many papers to deep-read after triage.

    Returns the same dict shape on success and failure (status field
    distinguishes); Celery serializes to JSON for the result backend.
    """
    verticals = verticals or []
    logger.info(
        f"[rr-task] run_radar_scan scan_id={scan_id} profile={profile_id!r} "
        f"topic={topic!r} verticals={verticals} top_n={top_n}"
    )
    try:
        return asyncio.run(
            service.run_scan_async(
                scan_id    = scan_id,
                profile_id = profile_id,
                topic      = topic,
                verticals  = verticals,
                top_n      = top_n,
            )
        )
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.exception(f"[rr-task] run_radar_scan failed at outer scope: {e}")
        runtime.service.emit_event_sync(
            scan_id, "error",
            message = f"task-outer: {err}",
        )
        # 2026-09-17: this outer handler was missing the `service.fail_scan`
        # call the INNER handler (`service.run_scan_pipeline`'s own
        # try/except) already has — confirmed live: scan
        # c01bf761 hit SoftTimeLimitExceeded here specifically (the
        # signal escaping the inner try/except, landing in this outer
        # one instead — visible from the "at outer scope" log line),
        # emitted the SSE error event, but the `radar_scans` Postgres
        # row was NEVER marked failed. It stayed `status='running',
        # finished_at=NULL` forever — anything computing elapsed time
        # from `started_at` with no `finished_at` shows an ever-
        # growing duration (this row was already ~4h40m "running" by
        # the time it was found, not a real 4-hour scan).
        try:
            asyncio.run(service.fail_scan(UUID(scan_id), err))
        except Exception as fe:
            logger.warning(f"[rr-task] outer-scope service.fail_scan also failed: {fe}")
        return {
            "scan_id":    scan_id,
            "profile_id": profile_id,
            "status":     "failed",
            "error":      err,
        }


# ---------------------------------------------------------------------------
# Build-tab code synthesis — dispatched by
# `POST /scan/{scan_id}/finding/{arxiv_id}/code/generate` so the HTTP layer
# never blocks on (or gateway-times-out on) the 1-5+ min generate→critique→
# revise loop. `GET /scan/{scan_id}/finding/{arxiv_id}/code` polls
# `runtime.service.get_code_synth_status` — set here to "running" on start
# and cleared on success (the MinIO cache written just before is then the
# source of truth), or set to "error" on failure so the operator sees a
# Retry button instead of a spinner stuck forever.
# ---------------------------------------------------------------------------

@infra.celery.service.app.task(
    name          = "domains.rr.task.run_code_synth",
    bind          = True,
    acks_late     = False,
    track_started = True,
)
def run_code_synth(self, scan_id: str, arxiv_id: str, prompt_version: str) -> dict:
    logger.info(
        f"[rr-task] run_code_synth scan_id={scan_id} arxiv_id={arxiv_id} "
        f"prompt_version={prompt_version}"
    )
    try:
        asyncio.run(service.run_code_synth_async(scan_id, arxiv_id, prompt_version))
        return {"scan_id": scan_id, "arxiv_id": arxiv_id, "status": "done"}
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.exception(
            f"[rr-task] run_code_synth failed scan_id={scan_id} "
            f"arxiv_id={arxiv_id}: {e}"
        )
        try:
            asyncio.run(
                runtime.service.set_code_synth_error(scan_id, arxiv_id, prompt_version, err)
            )
        except Exception as fe:
            logger.warning(f"[rr-task] run_code_synth set_code_synth_error also failed: {fe}")
        return {"scan_id": scan_id, "arxiv_id": arxiv_id, "status": "failed", "error": err}
