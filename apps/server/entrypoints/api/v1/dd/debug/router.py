"""DD development routes: direct stage-isolation helpers and queued tier benchmarks.

The benchmark endpoints use the regular Celery, lock and progress lifecycle,
but isolate their MinIO output. Other debug routes retain their direct dev
behavior. This router is not gated and is intended for the single-user dev cluster.
"""
import domains, infra
import domains.dd.ingestion.task
from . import params, schemas
from .. import catalog

import json
import logging
import re
import time

from fastapi import APIRouter, HTTPException


router = APIRouter()
logger = logging.getLogger(__name__)


def _valid_benchmark_round(round_id: str) -> bool:
    return re.fullmatch(
        domains.dd.ingestion.runtime.dispatch.params.BENCHMARK_ROUND_ID_PATTERN,
        round_id,
    ) is not None


@router.post("/benchmarks/{slug}")
async def start_tier_benchmark(
    slug: str,
    body: schemas.TierBenchmarkBody,
    entry: catalog.schemas.CatalogEntry,
) -> dict:
    """Queue an isolated, forced-tier run through extraction, normalization and publish."""
    dispatch = domains.dd.ingestion.runtime.dispatch
    dispatch_params = dispatch.params
    kind = dispatch_params.BENCHMARK_SOURCE_KINDS[body.tier - 1]
    source_url = entry.get(kind)
    if not isinstance(source_url, str) or not source_url:
        raise HTTPException(
            status_code=400,
            detail=f"{slug!r} has no {kind!r} URL in the catalog",
        )

    gate = await dispatch.service.reserve_run(slug, refresh=True)
    if gate["status"] != "reserved":
        return gate

    run_id = gate["run_id"]
    storage_slug = dispatch.domain.benchmark_storage_slug(slug, kind, run_id)
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "round_id": body.round_id,
        "framework_slug": slug,
        "framework_name": entry["name"],
        "requested_tier": kind,
        "source_url": source_url,
        "storage_slug": storage_slug,
        "status": "queued",
        "queued_at": time.time(),
        "source_attempts": [],
        "timings_ms": {},
        "build": {
            "service_version": infra.opentelemetry.params.SERVICE_VERSION,
            "git_sha": infra.opentelemetry.params.GIT_SHA,
            "normalizer_version": domains.dd.ingestion.corpus_normalize.versions.NORMALIZER_VERSION,
        },
    }
    minio = infra.minio.service.get_storage()
    progress = domains.dd.ingestion.runtime.progress.service.Progress(run_id)
    try:
        await minio.write(
            domains.dd.ingestion.storage.keys.benchmark_report_key(body.round_id, run_id),
            json.dumps(report, ensure_ascii=False, separators=(",", ":")),
            content_type=domains.dd.ingestion.storage.params.JSON_TYPE,
        )
        await progress.record_benchmark(report)
    except Exception as error:
        await dispatch.service.release_run(slug, run_id)
        raise HTTPException(
            status_code=503,
            detail=f"could not persist benchmark report before queueing: {type(error).__name__}",
        ) from error
    finally:
        await progress.close()

    try:
        task = domains.dd.ingestion.task.run_ingestion.delay(
            run_id,
            slug,
            source_kind=kind,
            benchmark_slug=storage_slug,
            round_id=body.round_id,
        )
    except Exception as error:
        report.update(
            status="failed",
            error=f"enqueue failed: {type(error).__name__}: {error}"[:500],
            finished_at=time.time(),
        )
        try:
            await minio.write(
                domains.dd.ingestion.storage.keys.benchmark_report_key(body.round_id, run_id),
                json.dumps(report, ensure_ascii=False, separators=(",", ":")),
                content_type=domains.dd.ingestion.storage.params.JSON_TYPE,
            )
        except Exception:
            logger.exception("failed to persist benchmark enqueue failure")
        failed_progress = domains.dd.ingestion.runtime.progress.service.Progress(run_id)
        try:
            await failed_progress.record_benchmark(report)
        finally:
            await failed_progress.close()
        await dispatch.service.release_run(slug, run_id)
        raise

    try:
        infra.opentelemetry.service.tag_celery_task(task.id)
    except Exception:
        logger.warning("could not attach the benchmark Celery task trace tag", exc_info=True)

    return {
        "status": "queued",
        "slug": slug,
        "run_id": run_id,
        "benchmark": {
            "round_id": body.round_id,
            "tier": body.tier,
            "source_kind": kind,
            "storage_slug": storage_slug,
        },
    }


@router.get("/benchmarks/{round_id}")
async def list_tier_benchmarks(round_id: str) -> dict:
    """Read the durable comparison reports for one benchmark round."""
    if not _valid_benchmark_round(round_id):
        raise HTTPException(status_code=400, detail="invalid benchmark round id")
    minio = infra.minio.service.get_storage()
    prefix = domains.dd.ingestion.storage.keys.benchmark_report_prefix(round_id)
    try:
        object_keys = await minio.list(prefix)
        reports = []
        for key in object_keys:
            if not key.endswith(".json"):
                continue
            try:
                report = json.loads(await minio.read_text(key))
            except (ValueError, UnicodeDecodeError):
                logger.warning("skipping malformed benchmark report %s", key)
                continue
            if isinstance(report, dict):
                reports.append(report)
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=f"could not read benchmark reports: {type(error).__name__}",
        ) from error
    reports.sort(key=lambda item: item.get("started_at") or item.get("queued_at") or 0)
    return {"round_id": round_id, "runs": reports}


@router.get("/benchmarks/{round_id}/{run_id}")
async def get_tier_benchmark(round_id: str, run_id: str) -> dict:
    """Read one benchmark report by round and ingestion run id."""
    if not _valid_benchmark_round(round_id) or re.fullmatch(r"[a-f0-9]{32}", run_id) is None:
        raise HTTPException(status_code=400, detail="invalid benchmark round or run id")
    minio = infra.minio.service.get_storage()
    key = domains.dd.ingestion.storage.keys.benchmark_report_key(round_id, run_id)
    try:
        if not await minio.exists(key):
            raise HTTPException(status_code=404, detail="benchmark report not found")
        report = json.loads(await minio.read_text(key))
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=f"could not read benchmark report: {type(error).__name__}",
        ) from error
    if not isinstance(report, dict):
        raise HTTPException(status_code=500, detail="benchmark report is malformed")
    return report




@router.post("/resolve/{slug}")
async def debug_resolve(
    slug: str, 
    entry: catalog.schemas.CatalogEntry) -> dict:
    dispatch = domains.dd.ingestion.runtime.dispatch.domain
    return {
        "slug": slug,
        "framework_name": entry["name"],
        "category": entry.get("category"),
        "best_source": dispatch.pick_best(entry),
        "all_sources": dispatch.source_chain(entry),
    }


@router.post("/ingest/{slug}")
async def debug_ingest_one_tier(
    slug: str,
    entry: catalog.schemas.CatalogEntry,
    tier: int,
    language: str | None = None,
) -> dict:
    """tier ∈ 1..5 (llms_full / llms_txt / sitemap / docs / github).
    Writes to the canonical MinIO path, overwriting same-idx bodies."""
    if tier not in params.KIND_BY_TIER:
        raise HTTPException(
            status_code=400,
            detail=f"tier must be 1..5, got {tier}",
        )
    kind = params.KIND_BY_TIER[tier]
    url = entry.get(kind)
    if not url:
        raise HTTPException(
            status_code=400,
            detail=f"{slug!r} has no {kind!r} URL in sources.yaml",
        )

    debug_run_id = f"debug-tier{tier}-{slug}-{int(time.time())}"
    progress = domains.dd.ingestion.runtime.progress.service.Progress(debug_run_id)
    minio = infra.minio.service.get_storage()
    async with infra.redis.service.client(op_timeout_s = domains.dd.ingestion.runtime.dispatch.params.REDIS_OP_TIMEOUT_S) as r:
        store = domains.dd.ingestion.storage.service.Store(
            debug_run_id,
            slug,
            r,
            minio)
        try:
            # The same call the dispatcher makes (the catalog's path_filter, code language, …), so a tier run here behaves as it does in a run.
            extra = {"language": language} if kind == "docs" and language is not None else {}
            try:
                await domains.dd.ingestion.runtime.dispatch.service.run_tier(
                    kind, url, entry = entry, slug = slug, progress = progress, store = store, **extra,
                )
            except domains.dd.ingestion.tiers.errors.ManifestDetected as e:
                return {
                    "status": "manifest_detected",
                    "slug": slug, "tier": kind,
                    "debug_run_id": debug_run_id,
                    "hint": "use tier=2 next; the body looks like a llms.txt index",
                    "details": str(e),
                }
            return {
                "status": "done",
                "slug": slug, "tier": kind, "url": url,
                "debug_run_id": debug_run_id,
                "pages_written": len(store.manifest),
                "total_bytes": sum(e.bytes for e in store.manifest),
            }
        finally:
            await progress.close()  # never raises: it swallows its own Redis errors


@router.post("/post/{slug}")
async def debug_post(
    slug: str, 
    entry: catalog.schemas.CatalogEntry) -> dict:
    """Re-runs post-process against current MinIO content (useful when
    tuning SPLIT_MIN_SECTION_BYTES without re-downloading)."""
    debug_run_id = f"debug-post-{slug}-{int(time.time())}"
    progress = domains.dd.ingestion.runtime.progress.service.Progress(debug_run_id)
    minio = infra.minio.service.get_storage()
    async with infra.redis.service.client(op_timeout_s = domains.dd.ingestion.runtime.dispatch.params.REDIS_OP_TIMEOUT_S) as r:
        try:
            store = await domains.dd.ingestion.storage.service.Store.from_existing(
                debug_run_id,
                slug,
                r,
                minio,
            )
            if not store.manifest:
                raise HTTPException(
                    status_code=404,
                    detail=f"no MinIO manifest for {slug!r} — run ingest first",
                )
            before = len(store.manifest)
            summary = await domains.dd.ingestion.post.service.apply_to_store(store)
            await progress.record_post(
                input_files=summary["input_files"],
                input_bytes=summary["input_bytes"],
                output_files=summary["output_files"],
                output_bytes=summary["output_bytes"],
                was_split=summary["was_split"],
                stubs_dropped=summary["stubs_dropped"],
                duplicates_dropped=summary["duplicates_dropped"],
                notes="debug-post",
            )
            return {
                "slug": slug,
                "debug_run_id": debug_run_id,
                "before_pages": before,
                "after_pages": len(store.manifest),
                "summary": summary,
            }
        finally:
            await progress.close()  # never raises: it swallows its own Redis errors


@router.post("/finalize/{slug}")
async def debug_finalize(
    slug: str, 
    entry: catalog.schemas.CatalogEntry) -> dict:
    """Re-writes the canonical manifest from prior in-memory state.
    Useful when the manifest payload shape changes."""
    debug_run_id = f"debug-finalize-{slug}-{int(time.time())}"
    minio = infra.minio.service.get_storage()
    async with infra.redis.service.client(op_timeout_s = domains.dd.ingestion.runtime.dispatch.params.REDIS_OP_TIMEOUT_S) as r:
        store = await domains.dd.ingestion.storage.service.Store.from_existing(
            debug_run_id,
            slug,
            r,
            minio)
        if not store.manifest:
            raise HTTPException(
                status_code=404,
                detail=f"no MinIO manifest for {slug!r} — run ingest first",
            )
        await store.finalize(
            extra={
                "framework_name": entry["name"],
                "run_id": debug_run_id,
                "note": "rewritten by debug-finalize",
        })
        return {
            "slug": slug,
            "debug_run_id": debug_run_id,
            "pages": len(store.manifest),
            "total_bytes": sum(e.bytes for e in store.manifest),
        }


@router.post("/snapshot/{slug}")
async def debug_take_snapshot(
    slug: str,
    entry: catalog.schemas.CatalogEntry, 
    label: str | None = None,
) -> dict:
    return await domains.dd.ingestion.storage.service.take_snapshot(
        infra.minio.service.get_storage(), 
        slug, 
        label=label)


@router.get("/snapshots/{slug}")
async def debug_list_snapshots(
    slug: str, 
    entry: catalog.schemas.CatalogEntry) -> dict:
    return {
        "slug": slug,
        "snapshots": await domains.dd.ingestion.storage.service.list_snapshots(
            infra.minio.service.get_storage(), 
            slug),
    }


@router.post("/restore/{slug}")
async def debug_restore_snapshot(
    slug: str, 
    entry: catalog.schemas.CatalogEntry, 
    ts: str) -> dict:
    try:
        return await domains.dd.ingestion.storage.service.restore_snapshot(
            infra.minio.service.get_storage(), 
            slug, 
            ts)
    except ValueError as e:
        raise HTTPException(
            status_code=404, 
            detail=str(e))


@router.delete("/snapshot/{slug}")
async def debug_delete_snapshot(
    slug: str, 
    entry: catalog.schemas.CatalogEntry, 
    ts: str) -> dict:
    return await domains.dd.ingestion.storage.service.delete_snapshot(
        infra.minio.service.get_storage(), 
        slug, 
        ts)
