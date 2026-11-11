"""Run, resume, and wipe Docs Distiller Agentic AI candidates over finalized pages, and connect page ingestion to its graph."""

from __future__ import annotations
import ai, domains, infra
from . import domain, params

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from pydantic import ValidationError


logger = logging.getLogger(__name__)


async def normalize_source(source: str) -> tuple[str, dict[str, int]]:
    """The page as the audit reads it: what a docs host prints on every page (the agent banner, the footer links, fence attributes, MDX
    wrappers) is removed by the deterministic normalizer first, so the model's calls go to what the page teaches and no repair can turn the
    furniture into content. A page the normalizer cannot read is audited as it came."""
    try:
        normalized = await asyncio.to_thread(
            domains.dd.ingestion.corpus_normalize.domain.normalize_doc, source,
        )
        counted = {
            name: value
            for name, value in normalized.stats.model_dump().items()
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
        }
        mechanical_fixes = {
            name: count
            for name, count in counted.items()
            if name in {
                "zero_width_chars_stripped", "non_whitespace_control_chars_stripped",
                "emoji_shortcode_heading_spaces_inserted",
                "fence_meta_stripped", "boundary_markers_stripped",
                "orphan_tags_stripped", "permalink_debris_stripped", "empty_media_stripped",
                "agent_banner_stripped", "license_header_stripped", "agent_footer_stripped",
                "footer_links_stripped", "heading_anchors_stripped", "code_annotations_stripped",
                "code_copy_lines_stripped", "toc_tables_stripped", "viewcode_links_stripped",
                "trailing_rules_stripped", "blank_lines_collapsed", "trailing_ws_lines",
            }
        }
        return normalized.body, mechanical_fixes
    except Exception as error:
        logger.warning(f"[dd-agentic] normalize_doc failed: {type(error).__name__}: {error}; the page is audited as it came")
        return source, {}


async def read_page_quality(
    framework_slug: str,
    page_idx: int,
    *,
    storage: infra.minio.service.MinIOStorage | None = None,
) -> dict[str, Any] | None:
    """Read the immutable audit/repair record for a page in the current finalized corpus."""
    if storage is None:
        storage = infra.minio.service.get_storage()
    manifest = await domains.dd.ingestion.storage.service.read_framework_manifest(
        storage, framework_slug,
    )
    run_id = (manifest or {}).get("agentic_run_id")
    if not isinstance(run_id, str) or not run_id:
        return None
    if domains.dd.ingestion.storage.domain.entry_with_idx(
        (manifest or {}).get("entries", []), page_idx,
    ) is None:
        return None

    key = domains.dd.ingestion.storage.keys.agentic_assessment_key(
        framework_slug, run_id, page_idx,
    )
    try:
        artifact = json.loads(await storage.read_text(key))
    except (FileNotFoundError, ValueError):
        return None
    if not isinstance(artifact, dict) or not isinstance(artifact.get("assessment"), dict):
        return None

    try:
        assessment = ai.dd.agents.schemas.PageAssessment.model_validate(artifact["assessment"])
    except ValidationError as error:
        logger.warning(
            f"[dd-quality] {framework_slug}: page {page_idx} has an invalid assessment artifact: "
            f"{error.__class__.__name__}"
        )
        return None
    patches = artifact.get("patches") or {}
    if not isinstance(patches, dict):
        patches = {}

    def patches_by_issue(name: str) -> dict[str, dict]:
        rows = patches.get(name)
        if not isinstance(rows, list):
            return {}
        return {
            patch["issue_id"]: patch
            for patch in rows
            if isinstance(patch, dict) and isinstance(patch.get("issue_id"), str)
        }

    accepted = patches_by_issue("accepted")
    rejected = patches_by_issue("rejected")
    repair = artifact.get("repair") or {}
    if not isinstance(repair, dict):
        repair = {}
    audit_unavailable = artifact.get("audit_status") == "unavailable"
    try:
        eligible_issues = int(repair.get("eligible_issues") or 0)
    except (TypeError, ValueError):
        eligible_issues = 0
    repair_unavailable = repair.get("status") == "unavailable" and eligible_issues > 0
    review_ids = set(rejected)
    if repair_unavailable or audit_unavailable:
        eligible_ids = repair.get("eligible_issue_ids")
        if repair_unavailable and isinstance(eligible_ids, list):
            review_ids.update(
                issue_id
                for issue_id in eligible_ids
                if isinstance(issue_id, str) and issue_id not in accepted
            )
        else:
            review_ids.update(issue.issue_id for issue in assessment.issues if issue.issue_id not in accepted)

    issues = []
    for issue in assessment.issues:
        accepted_patch = accepted.get(issue.issue_id)
        rejected_patch = rejected.get(issue.issue_id)
        rejected_proposal = (rejected_patch or {}).get("proposal")
        if not isinstance(rejected_proposal, dict):
            rejected_proposal = {}
        issues.append({
            "issue_id": issue.issue_id,
            "code": issue.category.value,
            "severity": issue.severity.value,
            "description": issue.description,
            "evidence": [text for text in (issue.evidence, issue.other_evidence) if text],
            "resolution": (
                "auto_applied" if accepted_patch is not None
                else "review" if issue.issue_id in review_ids
                else "none"
            ),
            "proposal": (
                {
                    "target_text": rejected_proposal.get("target_text", ""),
                    "replacement": rejected_proposal.get("replacement", ""),
                    "rationale": rejected_proposal.get("rationale", ""),
                    "confidence": rejected_proposal.get("confidence", 0.0),
                    "risk": rejected_patch.get("reason", "") if isinstance(rejected_patch.get("reason", ""), str) else "",
                }
                if rejected_patch is not None
                else None
            ),
        })

    mechanical_fixes = artifact.get("mechanical_fixes")
    if isinstance(mechanical_fixes, dict):
        try:
            control_chars_removed = int(mechanical_fixes.get("non_whitespace_control_chars_stripped") or 0)
        except (TypeError, ValueError):
            control_chars_removed = 0
        if control_chars_removed > 0 and not any(issue["code"] == "control_characters" for issue in issues):
            issues.append({
                "issue_id": "normalizer-control-characters",
                "code": "control_characters",
                "severity": "medium",
                "description": (
                    f"Found {control_chars_removed} non-whitespace control character"
                    f"{'s' if control_chars_removed != 1 else ''}."
                ),
                "evidence": [],
                "resolution": "auto_applied",
                "proposal": None,
            })

    warnings = artifact.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    errors = [
        message
        for message in (artifact.get("audit_error"), repair.get("error"))
        if isinstance(message, str) and message
    ]
    return {
        "framework_slug": framework_slug,
        "agentic_run_id": run_id,
        "page": page_idx,
        "page_kind": assessment.page_kind.value,
        "status": "review_required" if review_ids or audit_unavailable else "done",
        "delivery_disposition": "include",
        "issues": issues,
        "mechanical_fixes": {
            name: value
            for name, value in (artifact.get("mechanical_fixes") or {}).items()
            if isinstance(name, str) and isinstance(value, int) and not isinstance(value, bool) and value > 0
        } if isinstance(artifact.get("mechanical_fixes"), dict) else {},
        "audit_warnings": [warning for warning in warnings if isinstance(warning, str)],
        "errors": errors,
    }


async def run_ingestion_agentic(store, *, progress=None, checkpointer=None) -> dict:
    """Freeze assembled page inputs, run the graph, then update the in-memory manifest."""
    # A worker resolves the LLM endpoint once at import, before the Settings store may answer; the agents below are
    # built from that cached value, so read the Settings-page endpoint again before building them.
    domains.settings.chat.service.refresh_endpoint(force=True)

    entries = store.manifest
    minio = store.minio
    page_refs: list[dict[str, Any]] = []
    retained_entries = []
    empty_pages_dropped = 0

    # The graph only receives immutable object references and compact metadata.
    for entry in entries:
        source_key = entry.source_key or entry.key
        input_key = domains.dd.ingestion.storage.keys.agentic_input_key(
            store.framework_slug,
            store.run_id,
            entry.idx,
            entry.slug,
        )
        if await minio.exists(input_key):
            # A failed run already froze this page (already normalized): resume on those exact bytes.
            source = await minio.read_text(input_key)
            raw_source = await minio.read_text(source_key)
            recalculated, mechanical_fixes = await normalize_source(raw_source)
            if recalculated != source:
                logger.warning(
                    f"[dd-agentic] {store.framework_slug}: page {entry.idx} normalization differs from frozen input; "
                    "preserving the frozen input and omitting normalization counters"
                )
                mechanical_fixes = {}
        else:
            source, mechanical_fixes = await normalize_source(await minio.read_text(source_key))
        if not source.strip():
            empty_pages_dropped += 1
            logger.info(
                f"[dd-agentic] {store.framework_slug}: dropping page {entry.idx} "
                "because normalization left no readable content"
            )
            stage_key = domains.dd.ingestion.storage.keys.stage_page_key(
                store.framework_slug,
                store.run_id,
                entry.idx,
                entry.slug,
            )
            if entry.key == stage_key:
                await store.delete_body_by_key(entry.key)
            continue

        retained_entries.append(entry)
        await domains.dd.ingestion.storage.service.write_immutable(
            minio,
            input_key,
            source,
            params.MARKDOWN_TYPE,
        )
        source_sha256 = domain.sha256_text(source)
        page_refs.append(
            {
                "idx": entry.idx,
                "slug": entry.slug,
                "url": entry.url,
                "source_path": entry.source_path,
                "tier": entry.tier,
                "title": entry.title,
                "section": entry.section,
                "notes": entry.notes,
                "lastmod": entry.lastmod,
                "anchors": list(entry.anchors),
                "bytes": len(source.encode("utf-8")),
                "source_key": input_key,
                "raw_key": entry.raw_key,
                "source_sha256": source_sha256,
                "mechanical_fixes": mechanical_fixes,
            }
        )

    entries = retained_entries
    if not page_refs:
        raise ValueError("Agentic normalization found no readable content pages after cleanup")

    candidate_digest = domain.candidate_digest(
        [(page["idx"], page["source_sha256"]) for page in page_refs]
    )
    if progress is not None:
        await progress.start(tier="agentic_normalization", total=len(page_refs))

    auditor = ai.dd.agents.ingestion.page_auditor.agent.page_auditor()
    repairer = ai.dd.agents.ingestion.page_repair.agent.page_repair(
        check = ai.dd.nodes.ingestion.page_repair.domain.check_patch,
    )

    async def read_page(key: str) -> str:
        return await minio.read_text(key)

    async def write_artifact(key: str, body: str, content_type: str) -> None:
        await domains.dd.ingestion.storage.service.write_immutable(
            minio,
            key,
            body,
            content_type,
        )

    graph = ai.dd.graphs.ingestion.graphs.ingestion_graph(checkpointer = checkpointer)
    dependencies = ai.dd.graphs.ingestion.entities.IngestionDependencies(
        read_page = read_page,
        artifact_exists = minio.exists,
        read_artifact = minio.read_text,
        write_artifact = write_artifact,
        auditor = auditor,
        repairer = repairer,
        progress = progress,
    )
    graph_config = {
        "configurable": {
            "thread_id": domain.thread_id(
                store.framework_slug, store.run_id, candidate_digest
            ),
        }
    }
    graph_input = {
        "framework_slug": store.framework_slug,
        "run_id": store.run_id,
        "pages": page_refs,
    }
    if checkpointer is not None:
        snapshot = await graph.aget_state(graph_config)
        if snapshot.next:
            graph_input = None
    result = await graph.ainvoke(graph_input, config = graph_config, context = dependencies)

    outputs = result.get("output_pages", [])
    by_idx = {page["idx"]: page for page in outputs}
    new_entries = []
    for entry in entries:
        page = by_idx.get(entry.idx)
        if page is None:
            raise RuntimeError(f"agentic graph omitted page index {entry.idx}")
        entry.key = page["key"]
        entry.bytes = page["bytes"]
        entry.title = page["title"]
        entry.raw_key = page["raw_key"]
        entry.source_key = page["source_key"]
        new_entries.append(entry)

    await store.replace_manifest(new_entries)
    if progress is not None:
        await progress.complete_stage(
            "agentic",
            current=len(page_refs),
            total=len(page_refs),
        )
    return {
        **result.get("summary", {}),
        "pages": len(outputs),
        "empty_pages_dropped": empty_pages_dropped,
        "normalizer": params.NORMALIZER_VERSION,
        "quality": result.get("quality_summary") or {},
    }


async def run_candidate(run_id: str, slug: str) -> dict:
    """Normalize a saved page set without repeating its documentation extraction."""
    minio = infra.minio.service.get_storage()
    redis = infra.redis.service.new_redis(
        op_timeout_s=domains.dd.ingestion.runtime.dispatch.params.REDIS_OP_TIMEOUT_S,
    )
    progress = domains.dd.ingestion.runtime.progress.service.Progress(run_id)
    store = None
    current_task = asyncio.current_task()
    if current_task is None:
        raise RuntimeError("Agentic AI task has no asyncio task context")
    watcher = asyncio.create_task(
        domains.dd.ingestion.runtime.dispatch.service.cancel_watcher(
            redis,
            run_id,
            current_task,
            slug=slug,
        ),
    )
    result: dict = {"run_id": run_id, "slug": slug}
    try:
        if not await domains.dd.ingestion.runtime.progress.service.renew_lock(
            redis, slug, run_id
        ):
            raise RuntimeError(f"framework lock is no longer held for {slug!r}")
        await progress.raise_if_cancelled()

        source_manifest = (
            await domains.dd.ingestion.storage.service.read_framework_manifest(
                minio, slug
            )
        )
        live_entries = await domains.dd.ingestion.storage.service.read_live_manifest(
            redis, run_id
        )
        if live_entries:
            entries = domains.dd.ingestion.storage.domain.entries_from_manifest(
                {"entries": live_entries}
            )
            store = domains.dd.ingestion.storage.service.Store(
                run_id, slug, redis, minio
            )
            await store.replace_manifest(entries)
        elif source_manifest:
            store = await domains.dd.ingestion.storage.service.Store.from_existing(
                run_id, slug, redis, minio
            )
            entries = store.manifest
        else:
            raise FileNotFoundError(
                f"no extracted documentation is available for {slug!r}"
            )

        if not entries:
            raise ValueError(f"no pages are available for Agentic AI in {slug!r}")

        # A failed run already froze its page inputs. Reuse those exact bytes so the same LangGraph thread resumes.
        for entry in entries:
            input_key = domains.dd.ingestion.storage.keys.agentic_input_key(
                slug,
                run_id,
                entry.idx,
                entry.slug,
            )
            if await minio.exists(input_key):
                entry.key = input_key
                entry.source_key = input_key
        await store.replace_manifest(entries)

        await progress.complete_stage(
            "extraction",
            current=len(entries),
            total=len(entries),
        )
        ai_result = await run_ingestion_agentic(
            store,
            progress=progress,
            checkpointer=infra.postgres.service.get_checkpointer(),
        )
        try:
            await domains.dd.ingestion.backfill.service.backfill_vaults_for_framework(
                slug,
                page_keys=[entry.key for entry in store.manifest],
            )
        except Exception as error:
            logger.warning(
                f"[dd-agentic] {slug}: vault backfill failed: {type(error).__name__}: {error}",
            )

        catalog_entry = domains.dd.catalog.service.index_by_slug().get(slug, {})
        await store.finalize(
            extra=domain.manifest_extra(
                source_manifest or {},
                catalog_entry.get("name", slug),
                run_id,
            )
        )
        await progress.finish(status="done")
        result.update(
            status="done", agentic=ai_result, pages_written=len(store.manifest)
        )
    except (
        domains.dd.ingestion.runtime.progress.errors.IngestCancelled,
        asyncio.CancelledError,
    ):
        logger.info(f"[dd-agentic] {slug}: candidate cancelled (run_id={run_id})")
        await progress.finish(status="cancelled")
        result["status"] = "cancelled"
    except Exception as error:
        logger.exception(f"[dd-agentic] {slug}: candidate failed (run_id={run_id})")
        await progress.finish(
            status="failed",
            error=domains.dd.ingestion.runtime.dispatch.domain.error_text(error),
        )
        result.update(status="failed", error=str(error))
    finally:
        watcher.cancel()
        try:
            await watcher
        except (asyncio.CancelledError, Exception):
            pass
        await domains.dd.ingestion.runtime.dispatch.service.release_run(slug, run_id)
        if store is not None:
            await store.close()
        await progress.close()
        await redis.aclose()
        await domains.settings.llm_counter.service.snapshot("dd", run_id)
    return result


async def resume_candidate(slug: str, run_id: str) -> int:
    """Reserve the framework and requeue only a failed or cancelled Agentic AI candidate."""
    if not domain.is_valid_run_id(run_id):
        raise ValueError("invalid Agentic AI run id")
    async with infra.redis.service.client() as redis:
        progress = await domains.dd.ingestion.runtime.progress.service.read_progress(
            redis, run_id
        )
        if not domain.is_resumable(progress):
            raise ValueError("only failed or cancelled Agentic AI runs can be resumed")
        live = await domains.dd.ingestion.storage.service.read_live_manifest(
            redis, run_id
        )
    manifest = await domains.dd.ingestion.storage.service.read_framework_manifest(
        infra.minio.service.get_storage(),
        slug,
    )
    page_count = len(live) or len((manifest or {}).get("entries", []))
    if page_count == 0:
        raise ValueError("no extracted documentation is available to resume")
    if not await domains.dd.ingestion.runtime.dispatch.service.reserve_existing_run(
        slug, run_id
    ):
        raise ValueError("another ingestion run is active")
    return page_count


async def wipe_candidate(slug: str, run_id: str) -> dict:
    """Remove AI artifacts and restore the persisted pre-AI page versions when this candidate is published."""
    if not domain.is_valid_run_id(run_id):
        raise ValueError("invalid Agentic AI run id")

    minio = infra.minio.service.get_storage()
    wipe_id = f"wipe-{uuid.uuid4().hex}"
    async with infra.redis.service.client() as redis:
        progress = await domains.dd.ingestion.runtime.progress.service.read_progress(
            redis, run_id
        )
        if isinstance(
            progress, dict
        ) and domains.dd.ingestion.runtime.progress.domain.is_active(progress):
            raise ValueError("stop Agentic AI and wait for it to finish before wiping")
        if not await domains.dd.ingestion.runtime.progress.service.acquire_lock(
            redis,
            slug,
            wipe_id,
        ):
            raise ValueError("another ingestion run is active for this framework")
        try:
            manifest = (
                await domains.dd.ingestion.storage.service.read_framework_manifest(
                    minio, slug
                )
            )
            candidate_key = domains.dd.ingestion.storage.keys.agentic_manifest_key(
                slug, run_id
            )
            candidate = None
            if await minio.exists(candidate_key):
                candidate = domains.dd.ingestion.storage.domain.parse_json(
                    await minio.read_text(candidate_key),
                    None,
                )
            candidate_pages = domain.candidate_pages_by_idx(candidate)

            restored_entries = []
            if manifest and manifest.get("agentic_run_id") == run_id:
                entries = domains.dd.ingestion.storage.domain.entries_from_manifest(
                    manifest
                )
                for entry in entries:
                    source_key = domain.original_source_key(
                        slug, run_id, entry, candidate_pages.get(entry.idx, {})
                    )
                    if not await minio.exists(source_key):
                        raise ValueError(
                            f"the saved original page for idx={entry.idx} is unavailable"
                        )
                    body = await minio.read_text(source_key)
                    entry.key = source_key
                    entry.source_key = source_key
                    entry.bytes = len(body.encode("utf-8"))
                    entry.title = domains.dd.ingestion.post.domain.display_title(
                        body, entry.slug
                    )
                    restored_entries.append(entry)

                await minio.write(
                    domains.dd.ingestion.storage.keys.manifest_key(slug),
                    json.dumps(
                        domain.restored_manifest(
                            manifest, restored_entries, time.time()
                        ),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    content_type=params.JSON_TYPE,
                )

            digest = await _candidate_digest(
                slug, run_id, candidate_pages, redis, minio
            )
            if digest:
                checkpointer = await infra.postgres.service.init_checkpointer()
                await checkpointer.adelete_thread(
                    domain.thread_id(slug, run_id, digest)
                )

            prefix = domains.dd.ingestion.storage.keys.agentic_prefix(slug, run_id)
            deleted = 0
            for part in params.CANDIDATE_PARTS:
                deleted += await minio.delete_prefix(f"{prefix}{part}")
            if await minio.exists(candidate_key):
                await minio.delete(candidate_key)
                deleted += 1
            await domains.settings.llm_counter.service.delete_run("dd", run_id)
            await redis.delete(
                domains.dd.ingestion.runtime.progress.keys.progress_key(run_id),
                domains.dd.ingestion.runtime.progress.keys.cancel_key(run_id),
                domains.dd.ingestion.storage.keys.live_manifest_key(run_id),
                domains.dd.ingestion.runtime.progress.keys.post_key(run_id),
                domains.dd.ingestion.runtime.progress.keys.url_records_key(run_id),
            )

            if restored_entries:
                try:
                    await domains.dd.ingestion.backfill.service.backfill_vaults_for_framework(
                        slug,
                        page_keys=[entry.key for entry in restored_entries],
                    )
                except Exception as error:
                    logger.warning(
                        f"[dd-agentic] {slug}: restored-page vault backfill failed: "
                        f"{type(error).__name__}: {error}",
                    )
            return {
                "slug": slug,
                "run_id": run_id,
                "wiped": True,
                "objects_deleted": deleted,
            }
        finally:
            await domains.dd.ingestion.runtime.progress.service.release_lock(
                redis, slug, wipe_id
            )


async def _candidate_digest(
    slug: str, run_id: str, candidate_pages: dict, redis, minio
) -> str | None:
    """Resolve the exact graph thread from its frozen input hashes, including an interrupted pre-publish run."""
    if candidate_pages:
        refs = domain.page_refs_of(candidate_pages)
    else:
        live = await domains.dd.ingestion.storage.service.read_live_manifest(
            redis, run_id
        )
        refs = []
        for entry in live:
            key = domains.dd.ingestion.storage.keys.agentic_input_key(
                slug,
                run_id,
                entry.get("idx", 0),
                entry.get("slug", "page"),
            )
            if not await minio.exists(key):
                return None
            refs.append((entry["idx"], domain.sha256_text(await minio.read_text(key))))
    if not refs:
        return None
    return domain.candidate_digest(refs)
