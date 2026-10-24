"""synth API helpers — plan loading and chapter wipes with HTTP error mapping.

`load_plan` is the api-side home for "slug → plan dict or 404" (wrapping
`domains.dd.planner.service.load_plan` with HTTP error mapping) — the
same shape as `api/v1/dd/resolver/service.py`.

`wipe_one_chapter` is the per-chapter wipe (MinIO only) behind the
study wipe endpoint.
"""
from __future__ import annotations

import json
import logging

from fastapi import HTTPException

import domains, infra

logger = logging.getLogger(__name__)


async def load_plan(slug: str) -> dict:
    """The latest planner plan for `slug`. 404 when the planner never ran,
    500 when the plan blob exists but cannot be read."""
    key = domains.dd.planner.keys.plan_latest_key(slug)
    try:
        plan = await domains.dd.planner.service.load_plan(slug)
    except Exception as e:
        raise HTTPException(
            status_code = 500,
            detail = f"plan {key!r} unreadable: {type(e).__name__}: {e}",
        )
    if plan is None:
        raise HTTPException(
            status_code = 404,
            detail = (
                f"no planner plan for {slug!r} — run the planner first "
                f"(POST /planner/{slug})"
            ),
        )
    return plan


async def wipe_one_chapter(slug: str, chapter_id: str) -> dict:
    """Delete ONE chapter's MinIO outputs + caches (synth/{slug}/{chapter_id}/) so a
    single-chapter run recomputes it from scratch — without it a rerun just hits
    the chapter's cached outline/digest/sawc blobs, and a full wipe means redoing
    every chapter. MinIO only: Postgres checkpoints / Redis snapshots belong to
    run threads, not chapters, and the rest of the study stays intact."""
    if (
        not chapter_id or "/" in chapter_id or ".." in chapter_id
        or not chapter_id.startswith("ch-")
    ):
        raise HTTPException(
            status_code=400,
            detail=f"invalid chapter_id {chapter_id!r}; expected 'ch-…' without slashes",
        )
    # Never delete outputs a running study is still writing.
    try:
        # A single-chapter run (the strip's ▶ button) holds only the slug's synth lock, no study registry entry.
        if await domains.dd.synth.runtime.dispatch.service.run_in_flight(slug):
            raise HTTPException(
                status_code=409,
                detail=f"a synth run for {slug!r} is in flight — cancel it before wiping a chapter",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[synth-wipe] {slug}/{chapter_id}: active-study check skipped ({type(e).__name__})")

    minio = infra.minio.service.get_storage()
    try:
        n_minio = await minio.delete_prefix(f"synth/{slug}/{chapter_id}/")
    except Exception as e:
        logger.warning(f"[synth-wipe] MinIO delete failed for {slug}/{chapter_id}: {e}")
        n_minio = -1

    # Keep the persisted timing roll-up consistent with the chapter being gone.
    try:
        tkey = domains.dd.synth.keys.study_timing_key(slug)
        t = json.loads(await minio.read_text(tkey))
        gone = int((t.get("per_chapter_ms") or {}).pop(chapter_id, 0) or 0)
        if gone:
            t["total_wall_ms"] = max(int(t.get("total_wall_ms") or 0) - gone, 0)
            await minio.write(tkey, json.dumps(t, indent = 2), content_type = "application/json")
    except Exception:
        pass

    logger.info(f"[synth-wipe] {slug}/{chapter_id}: minio={n_minio} blobs")
    return {"slug": slug, "chapter_id": chapter_id, "minio_deleted": n_minio}
