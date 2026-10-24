"""Checkpoint-history pruning for the planner + synth threads. The saver itself (pool, loop-change re-open, `setup()`) lives in `infra.postgres.service`."""
from __future__ import annotations
import domains, infra

import logging
from datetime import datetime, timedelta, timezone


logger = logging.getLogger(__name__)


async def prune_old_threads(
    *, older_than_days: int = 30, dry_run: bool = True,
) -> dict:
    """Delete checkpoint history for threads with no work left to resume.

    LangGraph's own checkpoints/checkpoint_writes/checkpoint_blobs tables
    (shared by planner + synth, see synth/graph.py) grow one row per node
    per run forever — nothing before this pruned them (confirmed
    2026-09-26: zero DELETE/adelete_thread call sites against these
    tables outside `wipe_planner`'s manual per-slug endpoint, which also
    wipes the MinIO study-guide content — not appropriate for an
    unattended sweep, since the generated docs should outlive their own
    resume-state history).

    A thread is eligible only if BOTH hold:
      - `aget_state(...).next` is empty — the exact "nothing left to
        resume" signal `resume_planner_async` already relies on, so a
        thread that could still make forward progress is never touched,
        regardless of what its own `status` field says (synth and
        planner don't track status identically, so this is deliberately
        framework-native rather than state-shape-specific).
      - its latest checkpoint's `created_at` is older than
        `older_than_days`.

    Deletes via `AsyncPostgresSaver.adelete_thread()` — the library's own
    real, implemented method for this (verified 2026-09-26 via
    `inspect.getsource`; NOT `aprune`/`adelete_for_runs`, which exist on
    the class but are `raise NotImplementedError` stubs in the installed
    version). `wipe_planner`'s endpoint predates this check and still
    hand-rolls per-table SQL for its own bulk pattern-delete case (one
    `LIKE` matching many threads at once, which `adelete_thread` can't
    express since it takes exactly one thread_id) — not touched here,
    out of scope for this fix. Never touches MinIO or Redis. `dry_run=
    True` by default; callers that actually want to delete must opt in
    explicitly."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    async with await infra.postgres.service.connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT thread_id FROM checkpoints "
                "WHERE thread_id LIKE 'docs-distiller/%'"
            )
            candidate_ids = [row[0] for row in await cur.fetchall()]

    planner_graph = domains.dd.planner.graph.build_graph()
    synth_graph = domains.dd.synth.graph.build_graph()

    to_delete: list[str] = []
    skipped_active = 0
    skipped_recent = 0
    errors: list[str] = []
    for thread_id in candidate_ids:
        graph = (
            synth_graph
            if "/synth/" in thread_id or "/study/" in thread_id
            else planner_graph
        )
        config = {"configurable": {"thread_id": thread_id}}
        try:
            snap = await graph.aget_state(config)
        except Exception as e:
            errors.append(f"{thread_id}: aget_state failed ({type(e).__name__}: {e})")
            continue
        if snap.next:
            skipped_active += 1
            continue
        if not snap.created_at:
            # No timestamp to judge age by — leave it for a future sweep
            # rather than guess.
            skipped_recent += 1
            continue
        if datetime.fromisoformat(snap.created_at) > cutoff:
            skipped_recent += 1
            continue
        to_delete.append(thread_id)

    deleted = 0
    if not dry_run and to_delete:
        checkpointer = infra.postgres.service.get_checkpointer()
        for thread_id in to_delete:
            try:
                # Real, implemented method (verified 2026-09-26 via
                # inspect.getsource — unlike `aprune`/`adelete_for_runs`,
                # which exist on this class but are `raise
                # NotImplementedError` stubs in the installed version).
                # Deletes checkpoints/checkpoint_blobs/checkpoint_writes
                # for this one thread_id in a single pipelined cursor —
                # the library's own supported operation for exactly this,
                # rather than hand-rolled per-table SQL.
                await checkpointer.adelete_thread(thread_id)
                deleted += 1
            except Exception as e:
                errors.append(f"{thread_id}: adelete_thread failed ({type(e).__name__}: {e})")

    logger.info(
        f"[checkpointer] prune_old_threads(older_than_days={older_than_days}, "
        f"dry_run={dry_run}): {len(candidate_ids)} candidate(s), "
        f"{len(to_delete)} eligible, {deleted} deleted, "
        f"{skipped_active} still resumable, {skipped_recent} too recent, "
        f"{len(errors)} error(s)"
    )
    return {
        "older_than_days":     older_than_days,
        "dry_run":             dry_run,
        "candidates":          len(candidate_ids),
        "eligible_thread_ids": to_delete,
        "threads_deleted":     deleted,
        "skipped_active":      skipped_active,
        "skipped_recent":      skipped_recent,
        "errors":              errors[:20],
    }
