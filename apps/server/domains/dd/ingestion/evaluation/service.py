"""Run selected frozen DD pages through the current normalizer, auditor and repairer without publishing."""
from __future__ import annotations

import ai
import domains
import infra

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from . import domain


logger = logging.getLogger(__name__)


class _ReplayFaultInjectingAuditor:
    """Inject fixture-scoped auditor failures without affecting live ingestion."""

    def __init__(self, auditor, failure_pages: set[int]) -> None:
        self._auditor = auditor
        self._failure_pages = failure_pages

    async def ainvoke(
        self,
        request: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> Any:
        messages = request.get("messages") if isinstance(request, dict) else None
        content = messages[0].get("content") if isinstance(messages, list) and messages else None
        try:
            page_index = json.loads(content).get("page_index") if isinstance(content, str) else None
        except (AttributeError, TypeError, ValueError):
            page_index = None
        if page_index in self._failure_pages:
            raise RuntimeError(f"Replay-injected auditor provider failure for fixture page {page_index}")
        return await self._auditor.ainvoke(request, config = config)


def _bounded_issue_summaries(quality_record: dict[str, Any]) -> list[dict[str, Any]]:
    assessment = quality_record.get("assessment")
    issues = assessment.get("issues") if isinstance(assessment, dict) else []
    if not isinstance(issues, list):
        return []

    summaries = []
    for issue in issues[:24]:
        if not isinstance(issue, dict):
            continue
        summary = {
            key: str(issue.get(key) or "")[:2_000]
            for key in ("issue_id", "category", "severity", "description", "evidence", "other_evidence")
        }
        confidence = issue.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            summary["confidence"] = confidence
        summaries.append(summary)
    return summaries


def _bounded_patch_summaries(quality_record: dict[str, Any]) -> list[dict[str, Any]]:
    patches = quality_record.get("patches")
    if not isinstance(patches, dict):
        return []

    summaries = []
    for disposition in ("accepted", "rejected"):
        rows = patches.get(disposition)
        if not isinstance(rows, list):
            continue
        for patch in rows[:24 - len(summaries)]:
            if not isinstance(patch, dict):
                continue
            summaries.append({
                "disposition": disposition,
                **{
                    key: str(patch.get(key) or "")[:1_000]
                    for key in ("issue_id", "rationale", "target_text", "replacement")
                },
            })
    return summaries


async def _save_report(minio, round_id: str, replay_id: str, report: dict[str, Any]) -> None:
    await minio.write(
        domains.dd.ingestion.storage.keys.evaluation_report_key(round_id, replay_id),
        json.dumps(report, ensure_ascii=False, separators=(",", ":")),
        content_type=domains.dd.ingestion.storage.params.JSON_TYPE,
    )


def _snapshot_sha256(rows: list[tuple[int, str]]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(rows), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def run_page_replay(
    replay_id: str,
    evaluation_round_id: str,
    source_round_id: str,
    source_run_id: str,
    fixture_ids: list[str],
    *,
    redis_client,
) -> dict[str, Any]:
    minio = infra.minio.service.get_storage()
    report_key = domains.dd.ingestion.storage.keys.evaluation_report_key(
        evaluation_round_id, replay_id,
    )
    report = json.loads(await minio.read_text(report_key))
    started = time.time()
    report.update(status="running", started_at=started)
    await _save_report(minio, evaluation_round_id, replay_id, report)

    try:
        rows = domain.fixtures()
        selected = [rows[fixture_id] for fixture_id in fixture_ids]
        source_report_key = domains.dd.ingestion.storage.keys.benchmark_report_key(
            source_round_id, source_run_id,
        )
        source_report = json.loads(await minio.read_text(source_report_key))
        if source_report.get("status") != "done":
            raise ValueError("source tier benchmark must be completed before its pages can be replayed")
        source_slug = source_report.get("storage_slug")
        if not isinstance(source_slug, str) or not source_slug.startswith("__dd_benchmark__-"):
            raise ValueError("source benchmark has no isolated storage namespace")
        source_manifest = await domains.dd.ingestion.storage.service.read_framework_manifest(
            minio, source_slug,
        )
        if not isinstance(source_manifest, dict):
            raise FileNotFoundError("source benchmark manifest is unavailable")
        if source_manifest.get("run_id") not in (None, source_run_id):
            raise ValueError("source manifest does not belong to the requested benchmark run")
        source_entries = domains.dd.ingestion.storage.domain.entries_from_manifest(source_manifest)
        by_idx = {entry.idx: entry for entry in source_entries}

        domains.settings.chat.service.refresh_endpoint(force=True)
        report["build"] = domain.provenance()
        replay_slug = f"__dd_benchmark__-replay-{source_run_id[:8]}-{replay_id}"
        page_refs = []
        replay_input_keys: dict[int, str] = {}
        source_hashes: list[tuple[int, str]] = []
        page_checks: dict[int, dict[str, Any]] = {}
        normalization_ms = 0

        for fixture in selected:
            page_idx = int(fixture["page_idx"])
            entry = by_idx.get(page_idx)
            if entry is None:
                raise ValueError(f"fixture {fixture['id']!r} refers to missing source page {page_idx}")
            source_key = entry.source_key or domains.dd.ingestion.storage.keys.agentic_input_key(
                source_slug, source_run_id, entry.idx, entry.slug,
            )
            source_body = await minio.read_text(source_key)
            original_sha = hashlib.sha256(source_body.encode("utf-8")).hexdigest()
            source_hashes.append((page_idx, original_sha))

            normalize_started = time.monotonic()
            normalized_body, mechanical_fixes = await domains.dd.ingestion.ai.service.normalize_source(
                source_body,
            )
            normalization_ms += max(0, int((time.monotonic() - normalize_started) * 1000))
            output_sha = hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()
            replay_input_key = domains.dd.ingestion.storage.keys.agentic_input_key(
                replay_slug, replay_id, entry.idx, entry.slug,
            )
            await domains.dd.ingestion.storage.service.write_immutable(
                minio,
                replay_input_key,
                normalized_body,
                domains.dd.ingestion.ai.params.MARKDOWN_TYPE,
            )
            replay_input_keys[entry.idx] = replay_input_key
            page_refs.append({
                "idx": entry.idx,
                "slug": entry.slug,
                "agent_framework_slug": source_report.get("framework_slug") or entry.slug,
                "url": entry.url,
                "source_path": entry.source_path,
                "tier": entry.tier,
                "title": entry.title,
                "section": entry.section,
                "notes": entry.notes,
                "lastmod": entry.lastmod,
                "anchors": list(entry.anchors),
                "bytes": len(normalized_body.encode("utf-8")),
                "source_key": replay_input_key,
                "raw_key": entry.raw_key,
                "source_sha256": output_sha,
                "mechanical_fixes": mechanical_fixes,
            })
            page_checks[page_idx] = {
                "fixture": fixture,
                "source_key": source_key,
                "source_body": source_body,
                "normalized_body": normalized_body,
                "normalization_stats": mechanical_fixes,
                "input_sha256": original_sha,
                "normalized_sha256": output_sha,
            }

        report["source_snapshot"] = {
            "sha256": _snapshot_sha256(source_hashes),
            "pages": [
                {"idx": page_idx, "sha256": source_sha}
                for page_idx, source_sha in sorted(source_hashes)
            ],
        }
        report["normalization"] = {
            "pages": len(page_refs),
            "duration_ms": normalization_ms,
            "version": domains.dd.ingestion.corpus_normalize.versions.NORMALIZER_VERSION,
        }
        await _save_report(minio, evaluation_round_id, replay_id, report)

        auditor = ai.dd.agents.ingestion.page_auditor.agent.page_auditor()
        audit_failure_pages = {
            int(fixture["page_idx"])
            for fixture in selected
            if (fixture.get("replay_faults") or {}).get("audit_provider_error") is True
        }
        if audit_failure_pages:
            auditor = _ReplayFaultInjectingAuditor(auditor, audit_failure_pages)
        repairer = ai.dd.agents.ingestion.page_repair.agent.page_repair(
            check=ai.dd.nodes.ingestion.page_repair.domain.check_patch,
        )
        progress_lock = asyncio.Lock()
        progress = {"phase": "audit", "completed": 0, "total": len(page_refs)}

        async def set_progress(phase: str, *, reset: bool = False) -> None:
            async with progress_lock:
                if reset or phase != progress["phase"]:
                    progress.update(phase=phase, completed=0, total=len(page_refs))
                else:
                    progress["completed"] += 1
                report["progress"] = dict(progress)
                await _save_report(minio, evaluation_round_id, replay_id, report)

        class CancellationCheck:
            async def raise_if_cancelled(self) -> None:
                cancelled = await domains.dd.ingestion.runtime.cancel.service.is_cancelled(
                    redis_client, replay_id,
                )
                if cancelled:
                    raise domains.dd.ingestion.runtime.progress.errors.IngestCancelled()

        async def read_page(key: str) -> str:
            return await minio.read_text(key)

        async def artifact_exists(key: str) -> bool:
            return await minio.exists(key)

        async def read_artifact(key: str) -> str:
            return await minio.read_text(key)

        async def write_artifact(key: str, body: str, content_type: str) -> None:
            await domains.dd.ingestion.storage.service.write_immutable(
                minio, key, body, content_type,
            )

        async def audit_page_done() -> None:
            await set_progress("audit")

        async def repair_page_done() -> None:
            await set_progress("repair")

        state = {
            "framework_slug": replay_slug,
            "run_id": replay_id,
            "pages": page_refs,
        }
        await set_progress("audit", reset=True)
        audit_started = time.monotonic()
        audited = await ai.dd.nodes.ingestion.audit_batch.service.audit_pages(
            state,
            read_page,
            artifact_exists,
            read_artifact,
            write_artifact,
            auditor,
            CancellationCheck(),
            audit_page_done,
        )
        audit_ms = max(0, int((time.monotonic() - audit_started) * 1000))
        report["audit"] = {"duration_ms": audit_ms, "pages": len(audited["assessments"])}
        await _save_report(minio, evaluation_round_id, replay_id, report)

        await set_progress("repair", reset=True)
        repair_started = time.monotonic()
        normalized = await ai.dd.nodes.ingestion.normalize_pages.service.normalize_pages(
            {**state, "assessments": audited["assessments"]},
            read_page,
            artifact_exists,
            read_artifact,
            write_artifact,
            repairer,
            CancellationCheck(),
            repair_page_done,
        )
        repair_ms = max(0, int((time.monotonic() - repair_started) * 1000))

        pages = []
        for output in normalized.get("output_pages", []):
            idx = int(output["idx"])
            quality_record = json.loads(await minio.read_text(output["assessment_key"]))
            input_info = page_checks[idx]
            output_body = await minio.read_text(output["key"])
            evaluation = domain.evaluate_page(
                input_info["fixture"],
                input_info["normalization_stats"],
                input_info["source_body"],
                input_info["normalized_body"],
                output_body,
                quality_record,
            )
            issue_summaries = _bounded_issue_summaries(quality_record)
            pages.append({
                "fixture_id": input_info["fixture"]["id"],
                "idx": idx,
                "labels": input_info["fixture"].get("labels", []),
                "replay_faults": input_info["fixture"].get("replay_faults", {}),
                "input_sha256": input_info["input_sha256"],
                "normalized_input_sha256": input_info["normalized_sha256"],
                "output_sha256": quality_record.get("output_sha256"),
                "normalization_stats": input_info["normalization_stats"],
                "audit_status": quality_record.get("audit_status"),
                "repair_status": (quality_record.get("repair") or {}).get("status"),
                "accepted_repairs": len((quality_record.get("patches") or {}).get("accepted") or []),
                "rejected_repairs": len((quality_record.get("patches") or {}).get("rejected") or []),
                "issue_categories": [issue["category"] for issue in issue_summaries if issue.get("category")],
                "audit_findings": issue_summaries,
                "patch_decisions": _bounded_patch_summaries(quality_record),
                "evaluation": evaluation,
                "artifacts": {
                    "source_key": input_info["source_key"],
                    "input_key": replay_input_keys[idx],
                    "assessment_key": output["assessment_key"],
                    "output_key": output["key"],
                },
            })

        failed_checks = [
            {"fixture_id": page["fixture_id"], **check}
            for page in pages
            for check in page["evaluation"]["failed_checks"]
        ]
        unavailable_audits = [page["fixture_id"] for page in pages if page["audit_status"] != "completed"]
        expected_unavailable_audits = [
            page["fixture_id"]
            for page in pages
            if page["audit_status"] != "completed"
            and (page_checks[page["idx"]]["fixture"].get("expected") or {}).get("audit_status")
            == page["audit_status"]
        ]
        unexpected_unavailable_audits = [
            page["fixture_id"]
            for page in pages
            if page["audit_status"] != "completed"
            and (page_checks[page["idx"]]["fixture"].get("expected") or {}).get("audit_status")
            != page["audit_status"]
        ]
        unavailable_repairs = [page["fixture_id"] for page in pages if page["repair_status"] == "unavailable"]
        try:
            llm_counters = await domains.settings.llm_counter.service.read_counters("dd", replay_id)
        except Exception as error:
            logger.warning(
                "could not attach DD replay LLM counters for %s: %s",
                replay_id,
                type(error).__name__,
            )
            llm_counters = None
        report.update(
            status="done",
            finished_at=time.time(),
            duration_s=max(0.0, time.time() - started),
            timings_ms={"normalization": normalization_ms, "audit": audit_ms, "repair": repair_ms},
            quality_summary=normalized.get("quality_summary", {}),
            llm_counters=llm_counters,
            pages=pages,
            deterministic_gate={
                "status": "failed" if failed_checks else "inconclusive" if unexpected_unavailable_audits or unavailable_repairs else "passed",
                "failed_checks": failed_checks,
                "unavailable_audits": unavailable_audits,
                "expected_unavailable_audits": expected_unavailable_audits,
                "unexpected_unavailable_audits": unexpected_unavailable_audits,
                "unavailable_repairs": unavailable_repairs,
            },
            human_review={
                "required": any(
                    page["evaluation"]["human_review_required"]
                    or page["audit_status"] != "completed"
                    or page["repair_status"] == "unavailable"
                    for page in pages
                ),
                "pending_fixture_ids": [page["fixture_id"] for page in pages],
                "note": "Deterministic checks are automated; nuanced content-quality labels require human disposition.",
            },
        )
        await _save_report(minio, evaluation_round_id, replay_id, report)
        return report
    except asyncio.CancelledError:
        report.update(status="cancelled", finished_at=time.time(), duration_s=max(0.0, time.time() - started))
        await _save_report(minio, evaluation_round_id, replay_id, report)
        raise
    except Exception as error:
        logger.exception("DD ingestion fixed-input replay failed: %s", replay_id)
        report.update(
            status="failed",
            error=f"{type(error).__name__}: {error}"[:1000],
            finished_at=time.time(),
            duration_s=max(0.0, time.time() - started),
        )
        await _save_report(minio, evaluation_round_id, replay_id, report)
        raise
