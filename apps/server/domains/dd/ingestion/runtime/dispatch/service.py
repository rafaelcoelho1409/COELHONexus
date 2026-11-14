"""Cancel is cooperative (progress.raise_if_cancelled + watcher pre-empts blocking awaits). The framework's lock is a short lease the
watcher keeps renewing while the run is alive, so a task that dies frees the framework within `progress.params.LOCK_LEASE_S`."""

from __future__ import annotations
import domains, infra
from . import domain, params
from ... import tiers

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable


logger = logging.getLogger(__name__)


_TIER_MODULES = {
    "llms_full": tiers.tier1,
    "llms_txt": tiers.tier2,
    "sitemap": tiers.tier3,
    "docs": tiers.tier4,
    "github": tiers.tier5,
}


async def run_tier(
    kind: str, url: str, *, entry: dict, slug: str, progress, store, **extra
) -> int:
    """Run the tier of source `kind` on `url`, handing it what the catalog `entry` gives that tier (`domain.tier_arguments`) and
    any `extra` arguments. The one place a source kind becomes a tier call: the dispatcher and the debug route both use it."""
    return await _TIER_MODULES[kind].service.run(
        url=url,
        framework_slug=slug,
        progress=progress,
        store=store,
        **domain.tier_arguments(kind, entry),
        **extra,
    )


async def run_source_chain(
    chain,
    progress,
    *,
    new_progress,
    run_tier,
    base_result: dict,
    slug: str,
    source_attempts: list[dict] | None = None,
    on_attempts: Callable[[list[dict]], Awaitable[None]] | None = None,
):
    """Run the first source in `chain` (`[{kind, url}]`, best first) that works, and return the `Progress` the winning
    tier used. A tier that says its source is unusable — a link index instead of content, a page of links with none to
    follow, a 404 or an HTML page — hands over to the next declared source with a fresh `Progress`; any other error
    ends the run as before. When nothing is left the run fails, naming every reason."""
    reasons: list[str] = []
    attempts = source_attempts if source_attempts is not None else []

    async def publish_attempts() -> None:
        base_result["source_attempts"] = attempts
        if on_attempts is not None:
            await on_attempts(attempts)

    for position, source in enumerate(chain):
        kind, url = source["kind"], source["url"]
        base_result["tier_kind"] = kind
        base_result["tier_url"] = url
        started = time.monotonic()
        attempt = {
            "tier_kind": kind,
            "url": url,
            "status": "running",
            "started_at": time.time(),
        }
        attempts.append(attempt)
        await publish_attempts()
        try:
            await run_tier(kind, url, progress)
            attempt.update(
                status="selected",
                finished_at=time.time(),
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )
            await publish_attempts()
            return progress
        except tiers.errors.FALL_THROUGH as unusable:
            reasons.append(f"{kind} ({url}): {unusable}")
            attempt.update(
                status="unusable",
                error=str(unusable)[:500],
                finished_at=time.time(),
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )
            await publish_attempts()
            if isinstance(unusable, tiers.errors.EmptyLinksDetected):
                # Tier 2 does not log its own downgrade (tier 1 does): without this marker the progress row would sit
                # at "running" and the UI trail would lose the fact that a fallback happened at all.
                await progress.record_url(
                    url,
                    status="downgrade",
                    tier=kind,
                    error_msg="zero links, falling through",
                )
            await progress.finish(status="downgrade")
            following = chain[position + 1] if position + 1 < len(chain) else None
            if following is None:
                break
            logger.info(
                f"[dispatch] {slug}: {kind} unusable ({type(unusable).__name__}) — "
                f"falling through to {following['kind']} ({following['url']})"
            )
            await progress.close()
            progress = new_progress()
        except BaseException as error:
            attempt.update(
                status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                error=f"{type(error).__name__}: {error}"[:500],
                finished_at=time.time(),
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            )
            await publish_attempts()
            raise
    raise RuntimeError(f"no declared source for {slug!r} worked: " + "; ".join(reasons))


async def cancel_watcher(
    redis_client: infra.redis.service.Redis,
    run_id: str,
    main_task: asyncio.Task,
    poll_interval_s: float = params.CANCEL_POLL_S,
    *,
    slug: str | None = None,
    renew_every_s: float | None = None,
) -> None:
    """Bypasses Progress throttle (calls is_cancelled() directly; sleep is the rate limit). Crawl4AI arun_many can block 30-60 s.
    Given the run's `slug` it is also the lock's heartbeat: every `renew_every_s` it renews the framework's lease, so the lock lives
    exactly as long as this loop does — a task that dies stops renewing and the lock lapses on its own."""
    if renew_every_s is None:
        renew_every_s = domains.dd.ingestion.runtime.progress.params.LOCK_RENEW_S
    renew_at = 0.0
    try:
        while not main_task.done():
            try:
                if await domains.dd.ingestion.runtime.cancel.service.is_cancelled(
                    redis_client, run_id
                ):
                    logger.info(
                        f"[dispatch] {run_id}: cancel flag detected by "
                        f"watcher → cancelling main task"
                    )
                    main_task.cancel()
                    return
                if slug is not None and time.monotonic() >= renew_at:
                    renew_at = time.monotonic() + renew_every_s
                    if not await domains.dd.ingestion.runtime.progress.service.renew_lock(
                        redis_client, slug, run_id
                    ):
                        logger.warning(
                            f"[dispatch] {slug}: the lock is held by another run now (run_id={run_id})"
                        )
            except Exception as e:  # noqa: BLE001 — the watcher must outlive a Redis hiccup
                logger.warning(f"[dispatch] cancel watcher Redis error: {e}")
            await asyncio.sleep(poll_interval_s)
    except asyncio.CancelledError:
        return


async def _cleanup_run_staging(minio, framework_slug: str, run_id: str) -> int:
    """Delete only temporary assembly objects; raw inputs and agentic candidates are immutable run artifacts."""
    try:
        n = await minio.delete_prefix(
            domains.dd.ingestion.storage.keys.stage_prefix(framework_slug, run_id)
        )
        logger.info(
            f"[dispatch] cleanup staging {framework_slug}/{run_id}: deleted {n} MinIO objects"
        )
        return n
    except Exception as e:
        logger.warning(f"[dispatch] cleanup {framework_slug} failed: {e}")
        return 0


async def _persist_benchmark_report(progress, minio, report: dict) -> None:
    """Keep benchmark status available during a run and durable after Redis progress expires."""
    await progress.record_benchmark(report)
    try:
        key = domains.dd.ingestion.storage.keys.benchmark_report_key(
            report["round_id"], report["run_id"],
        )
        await minio.write(
            key,
            json.dumps(report, ensure_ascii=False, separators=(",", ":")),
            content_type=domains.dd.ingestion.storage.params.JSON_TYPE,
        )
    except Exception as error:
        logger.warning(
            f"[dispatch] benchmark report write failed for {report.get('run_id')}: "
            f"{type(error).__name__}: {error}"
        )


async def mark_benchmark_task_failed(
    run_id: str,
    slug: str,
    source_kind: str,
    benchmark_slug: str,
    round_id: str,
    error: BaseException,
) -> None:
    """Record worker setup failures that happen before the dispatcher starts."""
    if (
        source_kind not in params.BENCHMARK_SOURCE_KINDS
        or re.fullmatch(params.BENCHMARK_ROUND_ID_PATTERN, round_id) is None
        or benchmark_slug != domain.benchmark_storage_slug(slug, source_kind, run_id)
    ):
        return
    minio = infra.minio.service.get_storage()
    key = domains.dd.ingestion.storage.keys.benchmark_report_key(round_id, run_id)
    try:
        report = json.loads(await minio.read_text(key))
        if not isinstance(report, dict):
            report = {}
    except Exception:
        report = {}
    if report.get("status") in {"done", "failed", "cancelled"}:
        return
    report.update(
        schema_version=1,
        run_id=run_id,
        round_id=round_id,
        framework_slug=slug,
        requested_tier=source_kind,
        storage_slug=benchmark_slug,
        status="failed",
        error=domain.error_text(error),
        finished_at=time.time(),
    )
    progress = domains.dd.ingestion.runtime.progress.service.Progress(run_id)
    try:
        await _persist_benchmark_report(progress, minio, report)
    finally:
        await progress.close()


async def reserve_run(slug: str, refresh: bool) -> dict:
    """Single-flight gate for starting an ingestion of `slug`. Returns a terminal response — `locked` (another
    ingestion is running, this slug's lock is held, or a concurrent start won the lock) or `cached` (a manifest
    exists and no refresh was asked) — or `{"status": "reserved", "run_id": ...}` with the slug's lock held and the
    cancel flag cleared: the caller enqueues the run and calls `release_run` if that fails. A refresh does not touch the
    stored copy here: each run writes immutable raw/agentic artifacts and replaces the canonical manifest only after publish."""
    progress = domains.dd.ingestion.runtime.progress.service
    async with infra.redis.service.client() as r:
        other = await progress.find_other_running(r, slug)
        if other is not None:
            running_slug, running_run_id = other
            return domain.locked_by_other(slug, running_slug, running_run_id)
        active = await progress.read_lock(r, slug)
        if active:
            return domain.locked_by_same(slug, active)

        if not refresh:
            minio = infra.minio.service.get_storage()
            cached = await domains.dd.ingestion.storage.service.read_framework_manifest(
                minio, slug
            )
            if cached:
                return domain.cached(slug, cached)

        run_id = uuid.uuid4().hex
        if not await progress.acquire_lock(r, slug, run_id):
            return domain.locked_by_race(slug, await progress.read_lock(r, slug))
        await domains.dd.ingestion.runtime.cancel.service.clear_cancel(r, run_id)
    return domain.reserved(slug, run_id)


async def reserve_existing_run(slug: str, run_id: str) -> bool:
    """Reacquire a framework lock for a resumable Agentic AI candidate."""
    progress = domains.dd.ingestion.runtime.progress.service
    async with infra.redis.service.client() as r:
        if await progress.find_other_running(r, slug) is not None:
            return False
        if await progress.read_lock(r, slug):
            return False
        if not await progress.acquire_lock(r, slug, run_id):
            return False
        await domains.dd.ingestion.runtime.cancel.service.clear_cancel(r, run_id)
    return True


async def release_run(slug: str, run_id: str) -> None:
    """Give the slug's lock back after a failed enqueue (best-effort: never raised, so it cannot mask the failure)."""
    try:
        async with infra.redis.service.client() as r:
            await domains.dd.ingestion.runtime.progress.service.release_lock(
                r, slug, run_id
            )
    except Exception:
        pass


async def run(
    run_id: str,
    slug: str,
    *,
    source_kind: str | None = None,
    benchmark_slug: str | None = None,
    round_id: str | None = None,
) -> dict:
    """Session + span + metrics wrapper around the ingestion dispatcher.
    The session wrapper was missing until now — every other DD/YCS
    pipeline groups its spans into one LangFuse trace via `session(...)`;
    ingestion had the span but not the session, so its spans never
    grouped into one trace in the LangFuse UI.

    2026-09-24: the LangFuse `session_id` is a friendly
    `docs-distiller/{slug}/{run_id}` label (matches planner's
    `docs-distiller/{slug}/...` and synth's
    `docs-distiller/study/{slug}/...`) instead of the bare `run_id` —
    ingestion was the only DD stage findable in the LangFuse UI only by
    trace ID. `run_id` itself is untouched everywhere else (Redis
    locks/progress keys, MinIO manifest paths, the `/ingestion/runs/{run_id}`
    REST routes all still use the plain uuid4 hex) — this only changes
    the cosmetic label LangFuse groups spans under."""
    t0 = asyncio.get_running_loop().time()
    attributes = domain.run_attributes(run_id, slug)
    if source_kind is not None:
        attributes.update(
            {
                "ingestion.benchmark": True,
                "ingestion.benchmark.tier": source_kind,
                "ingestion.benchmark.round_id": round_id or "",
            }
        )
    with (
        infra.langfuse.service.session(
            params.LANGFUSE_SESSION_NAME,
            session_id=domain.session_id(slug, run_id),
            user_id=slug,
            framework=slug,
        ),
        infra.opentelemetry.service.get_tracer().start_as_current_span(
            params.SPAN_NAME,
            attributes={
                **attributes,
                infra.langfuse.keys.KEEP: True,
            },
        ),
    ):
        result = await _run_inner(
            run_id,
            slug,
            source_kind=source_kind,
            benchmark_slug=benchmark_slug,
            round_id=round_id,
        )
        await domains.settings.llm_counter.service.snapshot("dd", run_id)
    domains.dd.ingestion.observability.service.record_ingestion_run(
        framework=slug,
        duration_s=max(asyncio.get_running_loop().time() - t0, 0.0),
        **domain.run_metrics(result),
    )
    return result


async def _run_inner(
    run_id: str,
    slug: str,
    *,
    source_kind: str | None = None,
    benchmark_slug: str | None = None,
    round_id: str | None = None,
) -> dict:
    """Framework lock held by run_id on entry (acquired in POST /ingestion/runs); released in finally."""
    benchmark = source_kind is not None or benchmark_slug is not None or round_id is not None

    async def record_benchmark_rejection(message: str) -> None:
        if (
            benchmark
            and isinstance(source_kind, str)
            and isinstance(benchmark_slug, str)
            and isinstance(round_id, str)
        ):
            await mark_benchmark_task_failed(
                run_id,
                slug,
                source_kind,
                benchmark_slug,
                round_id,
                RuntimeError(message),
            )

    catalog = domains.dd.catalog.service.index_by_slug()
    entry = catalog.get(slug)
    if entry is None:
        error = f"unknown framework slug: {slug!r}"
        await record_benchmark_rejection(error)
        return domain.rejected_result(run_id, slug, error)

    if benchmark:
        if (
            source_kind not in params.BENCHMARK_SOURCE_KINDS
            or re.fullmatch(r"[a-f0-9]{32}", run_id) is None
            or not isinstance(benchmark_slug, str)
            or benchmark_slug != domain.benchmark_storage_slug(slug, source_kind, run_id)
            or not isinstance(round_id, str)
            or re.fullmatch(params.BENCHMARK_ROUND_ID_PATTERN, round_id) is None
        ):
            await record_benchmark_rejection("invalid tier benchmark configuration")
            return domain.rejected_result(
                run_id,
                slug,
                "invalid tier benchmark configuration",
            )
        source_url = entry.get(source_kind)
        if not isinstance(source_url, str) or not source_url:
            error = f"catalog entry has no source URL for forced tier {source_kind!r}"
            await record_benchmark_rejection(error)
            return domain.rejected_result(
                run_id,
                slug,
                error,
            )
        best = {"kind": source_kind, "url": source_url}
    else:
        best = domain.pick_best(entry)
    if best is None:
        return domain.rejected_result(
            run_id,
            slug,
            "no source URLs in catalog entry",
            framework_name=entry["name"],
        )

    progress = domains.dd.ingestion.runtime.progress.service.Progress(run_id)
    r = infra.redis.service.new_redis(op_timeout_s=params.REDIS_OP_TIMEOUT_S)
    minio = infra.minio.service.get_storage()
    storage_slug = benchmark_slug or slug
    store = domains.dd.ingestion.storage.service.Store(
        run_id,
        storage_slug,
        r,
        minio,
        agent_framework_slug=slug,
    )

    base_result = domain.base_result(run_id, slug, entry, best)
    source_attempts: list[dict] = []
    benchmark_report = None
    started_at = time.time()
    if benchmark:
        queued_at = None
        build_info = {}
        try:
            queued_report = json.loads(
                await minio.read_text(
                    domains.dd.ingestion.storage.keys.benchmark_report_key(
                        round_id, run_id,
                    )
                )
            )
            if isinstance(queued_report, dict):
                queued_at = queued_report.get("queued_at")
                build_info = queued_report.get("build") or {}
        except Exception:
            pass
        benchmark_report = {
            "schema_version": 1,
            "run_id": run_id,
            "round_id": round_id,
            "framework_slug": slug,
            "framework_name": entry["name"],
            "requested_tier": source_kind,
            "source_url": best["url"],
            "storage_slug": storage_slug,
            "status": "running",
            "started_at": started_at,
            "queued_at": queued_at,
            "build": build_info if isinstance(build_info, dict) else {},
            "source_attempts": source_attempts,
        }
        base_result["tier_benchmark"] = {
            "round_id": round_id,
            "requested_tier": source_kind,
            "storage_slug": storage_slug,
        }
        domains.settings.chat.service.refresh_endpoint(force=True)
        from domains.dd.ingestion.evaluation import domain as evaluation_domain

        benchmark_report["build"].update(evaluation_domain.provenance())
        await _persist_benchmark_report(progress, minio, benchmark_report)

    # Spawn watcher BEFORE the main try so it's active from the first await.
    watcher_task = asyncio.create_task(
        cancel_watcher(r, run_id, asyncio.current_task(), slug=slug),
    )

    try:
        # The lock was taken when the run was queued, with the longer queued lease: this run now keeps it alive as it goes. If another
        # run holds it by now (this one waited longer than even that lease for a free worker), this one stands down without touching
        # the framework's data — the failure handlers below would wipe the other run's partial copy.
        if not await domains.dd.ingestion.runtime.progress.service.renew_lock(
            r, slug, run_id
        ):
            result = domain.rejected_result(
                run_id,
                slug,
                "the framework's lock was taken by another run while this one waited to start",
                framework_name=entry["name"],
            )
            if benchmark_report is not None:
                benchmark_report.update(
                    status="failed",
                    error=result["error"],
                    finished_at=time.time(),
                    duration_s=max(0.0, time.time() - started_at),
                )
                await _persist_benchmark_report(progress, minio, benchmark_report)
            return result

        await progress.raise_if_cancelled()

        async def _chain_tier(kind: str, url: str, tier_progress) -> None:
            await run_tier(
                kind, url, entry=entry, slug=storage_slug, progress=tier_progress, store=store
            )

        chain = (
            [best]
            if benchmark
            else domain.source_chain(entry)
        )
        extraction_started = time.monotonic()

        async def _record_attempts(attempts: list[dict]) -> None:
            if benchmark_report is None:
                return
            benchmark_report["source_attempts"] = [dict(row) for row in attempts]
            await _persist_benchmark_report(progress, minio, benchmark_report)

        # Benchmarks force exactly one source kind; regular runs retain catalog priority and fallback behavior.
        progress = await run_source_chain(
            chain,
            progress,
            new_progress=lambda: domains.dd.ingestion.runtime.progress.service.Progress(
                run_id
            ),
            run_tier=_chain_tier,
            base_result=base_result,
            slug=slug,
            source_attempts=source_attempts,
            on_attempts=_record_attempts if benchmark else None,
        )

        await progress.raise_if_cancelled()

        await progress.complete_stage(
            "extraction",
            current=len(store.manifest),
            total=len(store.manifest),
        )
        if benchmark_report is not None:
            benchmark_report["extraction"] = {
                "pages": len(store.manifest),
                "bytes": sum(entry.bytes for entry in store.manifest),
                "source_attempts": [dict(row) for row in source_attempts],
            }
            try:
                capture_key = domains.dd.ingestion.storage.keys.raw_capture_key(
                    storage_slug, run_id, source_kind or "",
                )
                capture = json.loads(await minio.read_text(capture_key))
                captured_files = capture.get("files") if isinstance(capture, dict) else None
                if isinstance(captured_files, list):
                    file_snapshot = sorted(
                        (
                            {
                                "name": item.get("name", ""),
                                "sha256": item.get("sha256", ""),
                                "bytes": int(item.get("bytes") or 0),
                                "status": int(item.get("status") or 0),
                                "stored": bool(item.get("stored", False)),
                            }
                            for item in captured_files
                            if isinstance(item, dict)
                        ),
                        key=lambda item: item["name"],
                    )
                    snapshot_json = json.dumps(
                        file_snapshot, sort_keys=True, separators=(",", ":"),
                    )
                    benchmark_report["extraction"]["captured_source_snapshot"] = {
                        "sha256": hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
                        "files": len(file_snapshot),
                        "stored_files": sum(item["stored"] for item in file_snapshot),
                        "bytes": sum(item["bytes"] for item in file_snapshot),
                    }
            except Exception as error:
                benchmark_report["extraction"]["captured_source_snapshot"] = {
                    "status": "unavailable",
                    "error": type(error).__name__,
                }
            benchmark_report.setdefault("timings_ms", {})["extraction"] = max(
                0, int((time.monotonic() - extraction_started) * 1000)
            )
            await _persist_benchmark_report(progress, minio, benchmark_report)
        await progress.start(tier="post", total=0)

        phase_started = time.monotonic()
        post_summary = await domains.dd.ingestion.post.service.prepare_for_agentic(
            store
        )
        if benchmark_report is not None:
            benchmark_report.setdefault("timings_ms", {})["post"] = max(
                0, int((time.monotonic() - phase_started) * 1000)
            )
        await progress.record_post(
            **domain.post_record(base_result["tier_kind"], post_summary)
        )
        if benchmark_report is not None:
            benchmark_report["post"] = post_summary
            await _persist_benchmark_report(progress, minio, benchmark_report)

        await progress.raise_if_cancelled()

        phase_started = time.monotonic()
        ai_result = await domains.dd.ingestion.ai.service.run_ingestion_agentic(
            store,
            progress=progress,
            checkpointer=infra.postgres.service.get_checkpointer(),
        )
        if benchmark_report is not None:
            benchmark_report["agentic"] = ai_result
            benchmark_report.setdefault("timings_ms", {})["agentic"] = max(
                0, int((time.monotonic() - phase_started) * 1000)
            )
            try:
                benchmark_report["llm_counters"] = await domains.settings.llm_counter.service.read_counters(
                    "dd", run_id,
                )
            except Exception as error:
                logger.warning(
                    "[dispatch] benchmark LLM counters unavailable for %s: %s",
                    run_id,
                    type(error).__name__,
                )
            await _persist_benchmark_report(progress, minio, benchmark_report)

        # Vaults are derived from the final agentic page version and live beneath that agentic run.
        # Own tier + real total/current — on a large framework (400+ pages)
        # this was previously a single multi-second block with the progress
        # UI stuck showing "post" and no counter moving at all.
        await progress.start(tier="vault_backfill", total=post_summary["output_files"])

        async def _on_backfill_progress(done: int, total: int) -> None:
            await progress.update(current=done)

        phase_started = time.monotonic()
        vault_backfill_status = "done"
        try:
            await domains.dd.ingestion.backfill.service.backfill_vaults_for_framework(
                storage_slug,
                on_progress=_on_backfill_progress,
                page_keys=[entry.key for entry in store.manifest],
            )
        except Exception as e:
            vault_backfill_status = "failed"
            logger.warning(
                f"[dispatch] {slug}: post-ingest vault backfill failed: "
                f"{type(e).__name__}: {e}"
            )
            if benchmark_report is not None:
                benchmark_report["vault_backfill_error"] = f"{type(e).__name__}: {e}"

        if benchmark_report is not None:
            benchmark_report["vault_backfill"] = vault_backfill_status
            benchmark_report.setdefault("timings_ms", {})["vault_backfill"] = max(
                0, int((time.monotonic() - phase_started) * 1000)
            )
            await _persist_benchmark_report(progress, minio, benchmark_report)

        await progress.start(tier="finalize", total=0)

        manifest_extra = domain.finalize_extra(
            entry, base_result, run_id, getattr(store, "index_summary", "")
        )
        if benchmark_report is not None:
            manifest_extra["tier_benchmark"] = {
                "round_id": round_id,
                "run_id": run_id,
                "requested_tier": source_kind,
                "report_key": domains.dd.ingestion.storage.keys.benchmark_report_key(
                    round_id, run_id,
                ),
            }
        phase_started = time.monotonic()
        await store.finalize(extra=manifest_extra)
        if benchmark_report is not None:
            benchmark_report.setdefault("timings_ms", {})["finalize"] = max(
                0, int((time.monotonic() - phase_started) * 1000)
            )
            benchmark_report.update(
                status="done",
                published_pages=len(store.manifest),
                finished_at=time.time(),
                duration_s=max(0.0, time.time() - started_at),
            )
            await _persist_benchmark_report(progress, minio, benchmark_report)

        await _cleanup_run_staging(minio, storage_slug, run_id)

        await progress.finish(status="done")

        base_result["agentic"] = ai_result
        return domain.done_result(base_result, store.manifest, post_summary)

    except (
        domains.dd.ingestion.runtime.progress.errors.IngestCancelled,
        asyncio.CancelledError,
    ):
        logger.info(f"[dispatch] {slug}: cancelled by user (run_id={run_id})")
        # CRITICAL: stop watcher BEFORE cleanup — poll loop fires a second main_task.cancel() mid-cleanup, raising CancelledError inside delete_prefix and leaving MinIO stragglers (observed: 428).
        watcher_task.cancel()
        try:
            await watcher_task
        except (asyncio.CancelledError, Exception):
            pass
        await _cleanup_run_staging(minio, storage_slug, run_id)
        await progress.finish(status="cancelled")
        if benchmark_report is not None:
            benchmark_report.update(
                status="cancelled",
                finished_at=time.time(),
                duration_s=max(0.0, time.time() - started_at),
            )
            await _persist_benchmark_report(progress, minio, benchmark_report)
        return domain.cancelled_result(base_result)

    except Exception as e:
        logger.exception(f"[dispatch] {slug}: failed")
        # The previous canonical manifest remains active. Raw and partial agentic artifacts stay versioned for diagnosis/retry.
        await _cleanup_run_staging(minio, storage_slug, run_id)
        await progress.finish(status="failed", error=domain.error_text(e))
        if benchmark_report is not None:
            benchmark_report.update(
                status="failed",
                error=domain.error_text(e),
                finished_at=time.time(),
                duration_s=max(0.0, time.time() - started_at),
            )
            await _persist_benchmark_report(progress, minio, benchmark_report)
        return domain.failed_result(base_result, e)

    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await domains.dd.ingestion.runtime.progress.service.release_lock(
                r, slug, run_id
            )
        except Exception:
            pass
        try:
            await store.close()
        except Exception:
            pass
        try:
            await progress.close()
        except Exception:
            pass
        try:
            await r.aclose()
        except Exception:
            pass
