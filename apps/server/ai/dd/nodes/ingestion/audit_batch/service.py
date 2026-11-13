"""Concurrent page auditing with immutable MinIO results and cancellation."""
from __future__ import annotations
import domains
from . import params
from .. import page_audit
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


class Auditor(Protocol):
    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class CancellationCheck(Protocol):
    async def raise_if_cancelled(self) -> None: ...


async def audit_pages(
    state: graphs.ingestion.states.IngestionState,
    read_page: PageReader,
    artifact_exists: ArtifactExists,
    read_artifact: ArtifactReader,
    write_artifact: ArtifactWriter,
    auditor: Auditor,
    progress: CancellationCheck | None,
    page_done: PageProgress,
) -> dict[str, Any]:
    """Audit every frozen page reference and persist evidence-checked results."""
    framework_slug = state.get("framework_slug")
    run_id = state.get("run_id")
    if not isinstance(framework_slug, str) or not isinstance(run_id, str):
        raise ValueError("the ingestion graph state has no framework or run identity")
    semaphore = asyncio.Semaphore(params.PAGE_CONCURRENCY)

    async def audit_page(page: graphs.ingestion.entities.PageReference) -> dict[str, Any]:
        if progress is not None:
            await progress.raise_if_cancelled()
        markdown = await read_page(page["source_key"])
        source_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        if source_sha256 != page["source_sha256"]:
            raise ValueError(f"page {page['idx']} changed after its immutable agent input was frozen")
        max_page_chars = page_audit.params.MAX_PAGE_MARKDOWN_CHARS
        if len(markdown) > max_page_chars:
            raise ValueError(
                f"page {page['idx']} exceeds the {max_page_chars}-character audit limit; "
                "split or escalate it instead of truncating"
            )

        audit_key = domains.dd.ingestion.storage.keys.agentic_audit_key(
            framework_slug,
            run_id,
            page["idx"],
        )
        record = None
        if await artifact_exists(audit_key):
            try:
                candidate = json.loads(await read_artifact(audit_key))
                if (
                    candidate.get("source_sha256") == source_sha256
                    and isinstance(candidate.get("assessment"), dict)
                ):
                    agents.schemas.PageAssessment.model_validate(candidate["assessment"])
                    record = candidate
            except Exception:
                record = None

        if record is None:
            # A page the auditor cannot assess is still a valid extracted page: it goes on unaudited, and the record says so.
            audit_error = None
            with domains.settings.llm_counter.service.llm_context(
                ns = "dd",
                run_id = run_id,
                node_id = "page_auditor",
            ):
                try:
                    assessment, warnings = await page_audit.node.audit_page(
                        framework_slug,
                        page,
                        markdown,
                        auditor,
                        config = {
                            "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
                        },
                    )
                except Exception as error:
                    audit_error = f"{type(error).__name__}: {error}"
                    logger.warning(
                        f"[dd-agentic] {framework_slug}: page {page['idx']} audit unavailable, "
                        f"kept the page as extracted ({audit_error})"
                    )
                    assessment = agents.schemas.PageAssessment(
                        page_kind = agents.schemas.PageKind.OTHER,
                        confidence = 0.0,
                    )
                    warnings = [f"Page auditor unavailable ({audit_error}); the page was kept as extracted."]
            record = {
                "idx": page["idx"],
                "source_sha256": source_sha256,
                "assessment": assessment.model_dump(mode = "json"),
                "mechanical_fixes": page.get("mechanical_fixes", {}),
                "warnings": warnings,
                "audit_status": "unavailable" if audit_error else "completed",
                **({"audit_error": audit_error} if audit_error else {}),
            }
            await write_artifact(
                audit_key,
                json.dumps(record, ensure_ascii = False),
                domains.dd.ingestion.ai.params.JSON_TYPE,
            )
        elif page.get("mechanical_fixes"):
            # A retry may reuse an audit artifact written before normalization counters were attached.
            record = {**record, "mechanical_fixes": page["mechanical_fixes"]}

        await page_done()
        return record

    async def audit_one(page: graphs.ingestion.entities.PageReference) -> dict[str, Any]:
        async with semaphore:
            return await audit_page(page)

    pages = sorted(state.get("pages", []), key = lambda page: page["bytes"], reverse = True)
    audits = await _gather_cancel_on_failure(audit_one(page) for page in pages)
    return {"assessments": sorted(audits, key = lambda item: item["idx"])}


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
