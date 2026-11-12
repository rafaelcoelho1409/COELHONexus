"""Apply evidence-bound repairs and persist immutable normalized page versions."""
from __future__ import annotations
import domains
from . import params
from .. import page_audit, page_repair
from .... import agents, graphs

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from typing import Any, Protocol


logger = logging.getLogger(__name__)


PageReader = Callable[[str], Awaitable[str]]
ArtifactExists = Callable[[str], Awaitable[bool]]
ArtifactReader = Callable[[str], Awaitable[str]]
ArtifactWriter = Callable[[str, str, str], Awaitable[None]]
PageProgress = Callable[[], Awaitable[None]]


def _quality_row(audit: dict[str, Any], patches: dict[str, Any], repair: dict[str, Any]) -> dict[str, Any]:
    assessment = audit.get("assessment") or {}
    issues = assessment.get("issues") if isinstance(assessment, dict) else []
    issues = issues if isinstance(issues, list) else []
    accepted = patches.get("accepted") if isinstance(patches, dict) else []
    rejected = patches.get("rejected") if isinstance(patches, dict) else []
    mechanical_fixes = audit.get("mechanical_fixes") or {}
    return {
        "audit_status": audit.get("audit_status", "completed"),
        "repair_status": repair.get("status", "not_needed"),
        "issue_categories": [
            str(issue.get("category", "unknown"))
            for issue in issues
            if isinstance(issue, dict)
        ],
        "issue_severities": [
            str(issue.get("severity", "unknown"))
            for issue in issues
            if isinstance(issue, dict)
        ],
        "accepted_patches": len(accepted) if isinstance(accepted, list) else 0,
        "rejected_patches": len(rejected) if isinstance(rejected, list) else 0,
        "mechanical_fixes": {
            name: value
            for name, value in mechanical_fixes.items()
            if isinstance(name, str) and isinstance(value, int) and not isinstance(value, bool) and value > 0
        } if isinstance(mechanical_fixes, dict) else {},
    }


def _quality_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, int] = {}
    severities: dict[str, int] = {}
    mechanical_fixes: dict[str, int] = {}
    accepted_patches = rejected_patches = 0
    for row in rows:
        for category in row["issue_categories"]:
            categories[category] = categories.get(category, 0) + 1
        for severity in row["issue_severities"]:
            severities[severity] = severities.get(severity, 0) + 1
        for name, count in row["mechanical_fixes"].items():
            mechanical_fixes[name] = mechanical_fixes.get(name, 0) + count
        accepted_patches += row["accepted_patches"]
        rejected_patches += row["rejected_patches"]
    audit_unavailable = sum(row["audit_status"] == "unavailable" for row in rows)
    repair_unavailable = sum(row["repair_status"] == "unavailable" for row in rows)
    review_required = sum(
        row["audit_status"] == "unavailable"
        or row["repair_status"] == "unavailable"
        or row["rejected_patches"] > 0
        for row in rows
    )
    return {
        "pages": len(rows),
        "pages_with_issues": sum(bool(row["issue_categories"]) for row in rows),
        "issues": sum(len(row["issue_categories"]) for row in rows),
        "issues_by_category": dict(sorted(categories.items())),
        "issues_by_severity": dict(sorted(severities.items())),
        "accepted_patches": accepted_patches,
        "rejected_patches": rejected_patches,
        "audit_unavailable_pages": audit_unavailable,
        "repair_unavailable_pages": repair_unavailable,
        "review_required_pages": review_required,
        "mechanical_fixes": dict(sorted(mechanical_fixes.items())),
    }


class Repairer(Protocol):
    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class CancellationCheck(Protocol):
    async def raise_if_cancelled(self) -> None: ...


async def normalize_pages(
    state: graphs.ingestion.states.IngestionState,
    read_page: PageReader,
    artifact_exists: ArtifactExists,
    read_artifact: ArtifactReader,
    write_artifact: ArtifactWriter,
    repairer: Repairer,
    progress: CancellationCheck | None,
    page_done: PageProgress,
) -> dict[str, Any]:
    """Normalize all audited pages and retain rejected proposals for review."""
    framework_slug = state.get("framework_slug")
    run_id = state.get("run_id")
    if not isinstance(framework_slug, str) or not isinstance(run_id, str):
        raise ValueError("the ingestion graph state has no framework or run identity")
    assessments = {
        item["idx"]: item
        for item in state.get("assessments", [])
    }
    semaphore = asyncio.Semaphore(params.PAGE_CONCURRENCY)
    quality_rows: list[dict[str, Any]] = []

    async def normalize_page(page: graphs.ingestion.entities.PageReference) -> dict[str, Any]:
        if progress is not None:
            await progress.raise_if_cancelled()
        markdown = await read_page(page["source_key"])
        audit = assessments.get(page["idx"])
        if audit is None:
            raise ValueError(f"page {page['idx']} has no completed audit")
        source_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        if source_sha256 != audit["source_sha256"]:
            raise ValueError(f"page {page['idx']} changed after its audit")
        if len(markdown) > page_audit.params.MAX_PAGE_MARKDOWN_CHARS:
            raise ValueError(
                f"page {page['idx']} exceeds the {page_audit.params.MAX_PAGE_MARKDOWN_CHARS}-character "
                "repair limit; split or escalate it instead of truncating"
            )

        output_key = domains.dd.ingestion.storage.keys.agentic_page_key(
            framework_slug,
            run_id,
            page["idx"],
            page["slug"],
        )
        assessment_key = domains.dd.ingestion.storage.keys.agentic_assessment_key(
            framework_slug,
            run_id,
            page["idx"],
        )
        saved_normalization = None
        if await artifact_exists(assessment_key):
            try:
                candidate = json.loads(await read_artifact(assessment_key))
                if (
                    candidate.get("source_sha256") == audit["source_sha256"]
                    and candidate.get("normalization_status") == "complete"
                ):
                    saved_normalization = candidate
            except Exception:
                saved_normalization = None

        if saved_normalization is not None:
            if await artifact_exists(output_key):
                existing_body = await read_artifact(output_key)
                existing_hash = hashlib.sha256(existing_body.encode("utf-8")).hexdigest()
                if existing_hash != saved_normalization.get("output_sha256"):
                    raise RuntimeError(f"agentic page output conflict at {output_key}")
            else:
                persisted_patches = agents.schemas.PagePatchBatch.model_validate({
                    "patches": (saved_normalization.get("patches") or {}).get("accepted", []),
                })
                existing_body, _ = page_repair.domain.apply_patches(
                    markdown,
                    audit["source_sha256"],
                    audit["assessment"],
                    persisted_patches,
                )
                replay_hash = hashlib.sha256(existing_body.encode("utf-8")).hexdigest()
                if replay_hash != saved_normalization.get("output_sha256"):
                    raise RuntimeError(f"agentic patch replay did not reproduce {output_key}")
                await write_artifact(
                    output_key,
                    existing_body,
                    domains.dd.ingestion.ai.params.MARKDOWN_TYPE,
                )
            saved_patches = saved_normalization.get("patches") or {}
            saved_repair = saved_normalization.get("repair") or {}
            quality_rows.append(_quality_row(audit, saved_patches, saved_repair))
            await page_done()
            return {
                **page,
                "key": output_key,
                "bytes": len(existing_body.encode("utf-8")),
                "title": saved_normalization.get("output_title") or page["title"],
                "source_sha256": audit["source_sha256"],
                "assessment_key": assessment_key,
                "repair_status": (saved_normalization.get("repair") or {}).get("status", "completed"),
            }

        normalized = markdown
        patch_report = {"accepted": [], "rejected": []}
        # Only findings a patch could pass reach the repairer; a repair that cannot be obtained must not fail the run,
        # because the audited original is already a valid page.
        repair_input = page_repair.domain.repairable_assessment(audit["assessment"])
        repair = {
            "status": "not_needed",
            "total_issues": len(audit["assessment"]["issues"]),
            "eligible_issues": len(repair_input["issues"]),
            "eligible_issue_ids": [issue["issue_id"] for issue in repair_input["issues"]],
        }
        if repair_input["issues"]:
            with domains.settings.llm_counter.service.llm_context(
                ns = "dd",
                run_id = run_id,
                node_id = "page_repair",
            ):
                try:
                    normalized, patch_report = await page_repair.node.repair_page(
                        framework_slug,
                        page,
                        markdown,
                        audit["source_sha256"],
                        repair_input,
                        repairer,
                        config = {
                            "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                        },
                    )
                    repair["status"] = "completed"
                except Exception as error:
                    logger.warning(
                        f"[dd-agentic] {framework_slug}: page {page['idx']} repair unavailable, "
                        f"kept the audited original ({type(error).__name__}: {error})"
                    )
                    repair["status"] = "unavailable"
                    repair["error"] = f"{type(error).__name__}: {error}"

        output_title = page_repair.domain.output_title(
            normalized,
            page["slug"],
            audit["assessment"],
            fallback = domains.dd.ingestion.post.domain.display_title,
        )
        final_audit = {
            **audit,
            "patches": patch_report,
            "repair": repair,
            "normalization_status": "complete",
            "output_key": output_key,
            "output_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "output_title": output_title,
        }
        await write_artifact(
            assessment_key,
            json.dumps(final_audit, ensure_ascii = False),
            domains.dd.ingestion.ai.params.JSON_TYPE,
        )
        await write_artifact(
            output_key,
            normalized,
            domains.dd.ingestion.ai.params.MARKDOWN_TYPE,
        )
        quality_rows.append(_quality_row(audit, patch_report, repair))
        await page_done()
        return {
            **page,
            "key": output_key,
            "bytes": len(normalized.encode("utf-8")),
            "title": output_title,
            "source_sha256": audit["source_sha256"],
            "assessment_key": assessment_key,
            "repair_status": repair["status"],
        }

    async def normalize_one(page: graphs.ingestion.entities.PageReference) -> dict[str, Any]:
        async with semaphore:
            return await normalize_page(page)

    pages = sorted(state.get("pages", []), key = lambda page: page["bytes"], reverse = True)
    outputs = await _gather_cancel_on_failure(normalize_one(page) for page in pages)
    outputs.sort(key = lambda item: item["idx"])
    post_domain = domains.dd.ingestion.post.domain
    plain_titles = post_domain.strip_site_suffix([page["title"] for page in outputs])
    # A page split out of one bundle (llms-full.txt) shares the bundle's `url`; its own address is its `source_path`.
    unique_titles = post_domain.disambiguate_titles(
        [
            (title, page.get("source_path") or page.get("url") or "")
            for title, page in zip(plain_titles, outputs)
        ]
    )
    for page, title in zip(outputs, unique_titles):
        page["title"] = title
    return {"output_pages": outputs, "quality_summary": _quality_summary(quality_rows)}


async def _gather_cancel_on_failure(
    awaitables: Iterable[Coroutine[Any, Any, Any]],
) -> list[Any]:
    tasks = [asyncio.create_task(awaitable) for awaitable in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions = True)
        raise
