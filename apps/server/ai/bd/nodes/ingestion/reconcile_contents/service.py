"""Reconcile source contents entries and publish the final processed-book manifest."""
from __future__ import annotations
import domains
from .... import agents, graphs

import asyncio
import contextlib
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pydantic import ValidationError


logger = logging.getLogger(__name__)
PageReader = Callable[[str], str | None | Awaitable[str | None]]
ArtifactWriter = Callable[[str, str, str], Any]
CancelCheck = Callable[[], Awaitable[bool]]


class ContentsReconciler(Protocol):
    async def ainvoke(self, input: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]: ...


async def _read(reader: PageReader, key: str) -> str:
    value = reader(key)
    if inspect.isawaitable(value):
        value = await value
    if not isinstance(value, str):
        raise ValueError(f"processed-book artifact {key} could not be read")
    return value


async def _write(writer: ArtifactWriter, key: str, content: str) -> None:
    value = writer(key, content, "application/json")
    if inspect.isawaitable(value):
        await value


def _fallback_rows(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "outline_index": row["outline_index"],
        "title": row["title"],
        "level": row["level"],
        "source_page_number": row["candidates"][0]["source_page_number"],
        "confidence": 0.25,
        "review_required": True,
    } for row in batch]


def _validation_summary(error: Exception) -> str:
    if not isinstance(error, ValidationError):
        return type(error).__name__
    issues = error.errors(include_input = False, include_context = False, include_url = False)
    return "; ".join(
        f"{'.'.join(str(part) for part in issue['loc'])}:{issue['type']}"
        for issue in issues[:8]
    ) or type(error).__name__


async def _reconcile_batch(
    batch: list[dict[str, Any]],
    reconciler: ContentsReconciler,
    run_id: str,
    cancel_check: CancelCheck | None,
) -> tuple[list[dict[str, Any]], bool]:
    payload = {
        "task": "Correct each contents entry's physical page target using the supplied page candidates.",
        "entries": batch,
    }
    try:
        response = await _invoke_reconciler(reconciler, payload, run_id, cancel_check)
        structured = response.get("structured_response") if isinstance(response, dict) else None
        proposal = agents.schemas.ContentsReconciliationProposal.model_validate(structured)
        expected = {row["outline_index"]: row for row in batch}
        returned = {item.outline_index: item for item in proposal.entries}
        if len(returned) != len(proposal.entries) or set(returned) != set(expected):
            raise ValueError("the contents agent did not return every outline entry exactly once")
        result = []
        for index, source in expected.items():
            item = returned[index]
            allowed = {candidate["source_page_number"] for candidate in source["candidates"]}
            if item.source_page_number not in allowed or item.title != source["title"] or item.level != source["level"]:
                raise ValueError("the contents agent changed an entry or selected an unavailable page")
            result.append({
                "outline_index": index,
                "title": source["title"],
                "level": source["level"],
                "source_page_number": item.source_page_number,
                "confidence": item.confidence,
                "review_required": item.confidence < 0.8,
            })
        return result, True
    except (ValidationError, ValueError) as error:
        minimum_batch_size = domains.bd.ingestion.ai.params.CONTENTS_RECONCILIATION_MIN_BATCH_SIZE
        if len(batch) > minimum_batch_size:
            midpoint = len(batch) // 2
            logger.warning(
                "[bd-contents-reconciler] splitting invalid response batch size=%d validation=%s",
                len(batch),
                _validation_summary(error),
            )
            first_rows, first_succeeded = await _reconcile_batch(
                batch[:midpoint], reconciler, run_id, cancel_check,
            )
            second_rows, second_succeeded = await _reconcile_batch(
                batch[midpoint:], reconciler, run_id, cancel_check,
            )
            return first_rows + second_rows, first_succeeded and second_succeeded
        logger.warning(
            "[bd-contents-reconciler] using reviewed fallback after validation=%s batch_size=%d",
            _validation_summary(error),
            len(batch),
        )
        return _fallback_rows(batch), False
    except Exception as error:  # noqa: BLE001 — unresolved contents never blocks otherwise useful page publication.
        logger.warning(
            "[bd-contents-reconciler] using reviewed fallback after %s batch_size=%d",
            type(error).__name__,
            len(batch),
        )
        return _fallback_rows(batch), False


async def _invoke_reconciler(
    reconciler: ContentsReconciler,
    payload: dict[str, Any],
    run_id: str,
    cancel_check: CancelCheck | None,
) -> dict[str, Any]:
    """Poll the run's stop flag while waiting on the provider and cancel the request promptly."""
    async def invoke() -> dict[str, Any]:
        with domains.settings.llm_counter.service.llm_context(
            ns = "bd", run_id = run_id, node_id = "contents_reconciler",
        ):
            return await reconciler.ainvoke({"messages": [{
                "role": "user",
                "content": json.dumps(payload, ensure_ascii = False),
            }]}, config = {
                "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
            })

    if cancel_check is not None and await cancel_check():
        raise asyncio.CancelledError

    invoke_task = asyncio.create_task(invoke())
    try:
        while True:
            done, _ = await asyncio.wait(
                {invoke_task},
                timeout = domains.bd.ingestion.ai.params.AGENTIC_CANCEL_POLL_INTERVAL_SECONDS,
            )
            if invoke_task in done:
                return invoke_task.result()
            if cancel_check is not None and await cancel_check():
                raise asyncio.CancelledError
    except BaseException:
        if not invoke_task.done():
            invoke_task.cancel()
        with contextlib.suppress(BaseException):
            await invoke_task
        raise


async def reconcile_contents(
    state: graphs.ingestion.states.IngestionRunState,
    read_page: PageReader,
    write_artifact: ArtifactWriter,
    artifact_exists: Callable[[str], Awaitable[bool]],
    reconciler: ContentsReconciler,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    """Validate AI TOC page choices and publish a single canonical page-number map."""
    candidate_key = state.get("processed_book_candidate_key")
    if not isinstance(candidate_key, str):
        raise ValueError("contents reconciliation requires a materialized processed-book candidate")
    candidate_content = await _read(read_page, candidate_key)
    candidate = domains.bd.ingestion.ai.processed_book.schemas.ProcessedBookCandidate.model_validate_json(
        candidate_content,
    )
    if (
        candidate.book_id != state.get("book_id")
        or candidate.extraction_run_id != state.get("extraction_run_id")
        or candidate.agentic_run_id != state.get("agentic_run_id")
        or candidate.selection_revision != state.get("content_selection_revision")
        or candidate.selection_sha256 != state.get("content_selection_sha256")
    ):
        raise ValueError("the processed-book candidate is not pinned to this ingestion run")

    proposal_rows: list[dict[str, Any]] = []
    all_batches_succeeded = True
    batch_size = domains.bd.ingestion.ai.params.CONTENTS_RECONCILIATION_BATCH_SIZE
    candidates = [item.model_dump(mode = "json") for item in candidate.contents_candidates]
    for offset in range(0, len(candidates), batch_size):
        if cancel_check is not None and await cancel_check():
            raise asyncio.CancelledError
        rows, succeeded = await _reconcile_batch(
            candidates[offset:offset + batch_size],
            reconciler,
            candidate.agentic_run_id,
            cancel_check,
        )
        proposal_rows.extend(rows)
        all_batches_succeeded = all_batches_succeeded and succeeded

    included_source_pages = {page.source_page_number for page in candidate.pages}
    proposal_rows = [
        row for row in proposal_rows if row["source_page_number"] in included_source_pages
    ]
    mapped, mapping_status = domains.bd.ingestion.ai.processed_book.domain.remap_contents(
        proposal_rows,
        [page.model_dump(mode = "json") for page in candidate.pages],
    )
    if not candidates:
        contents_status = "unavailable"
    elif not all_batches_succeeded or mapping_status != "complete":
        contents_status = "needs_review"
    else:
        contents_status = "complete"
    if cancel_check is not None and await cancel_check():
        raise asyncio.CancelledError
    _, manifest_key, manifest_sha256 = await domains.bd.ingestion.ai.processed_book.service.publish_manifest(
        candidate,
        mapped,
        contents_status,
        domains.bd.ingestion.ai.versions.CONTENTS_RECONCILER_VERSION,
        _StorageWriter(read_page, write_artifact, artifact_exists),
    )
    return {
        "processed_book_key": manifest_key,
        "processed_book_sha256": manifest_sha256,
        "processed_book_revision": candidate.selection_revision,
        "processed_page_count": len(candidate.pages),
        "contents_status": contents_status,
    }


class _StorageWriter:
    """Adapter allowing shared processed-book persistence to use graph artifact callbacks."""

    def __init__(
        self,
        reader: PageReader,
        writer: ArtifactWriter,
        exists_callback: Callable[[str], Awaitable[bool]],
    ):
        self._reader = reader
        self._writer = writer
        self._exists_callback = exists_callback

    async def exists(self, key: str) -> bool:
        return await self._exists_callback(key)

    async def read_text(self, key: str) -> str:
        return await _read(self._reader, key)

    async def write(self, key: str, content: str, content_type: str = "application/json") -> None:
        result = self._writer(key, content, content_type)
        if inspect.isawaitable(result):
            await result
