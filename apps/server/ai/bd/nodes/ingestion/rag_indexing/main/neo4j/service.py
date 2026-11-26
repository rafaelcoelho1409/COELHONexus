"""Application service for Neo4j graph-indexing orchestration."""
from __future__ import annotations
import domains
from ...... import graphs

import asyncio
import logging
import random
from collections.abc import Coroutine, Iterable
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)


def _split_graph_rows(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split a failed graph batch while retaining page and section metadata."""
    if len(rows) > 1:
        midpoint = len(rows) // 2
        return [rows[:midpoint], rows[midpoint:]]
    if not rows:
        return []

    row = rows[0]
    content = str(row.get("content") or "")
    if len(content) <= 1_000:
        return []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max(1_000, len(content) // 2),
        chunk_overlap=120,
        add_start_index=True,
        separators=["\n\n", "\n", " ", ""],
    )
    parts = splitter.create_documents([content])
    if len(parts) < 2:
        return []
    return [[{
        **row,
        "content": part.page_content,
        "source_char_start": int(row.get("source_char_start") or 0)
        + int(part.metadata.get("start_index") or 0),
    }] for part in parts]


def _merge_graph_facts(extractions: list[dict[str, list[dict[str, Any]]]]) -> dict[str, list[dict[str, Any]]]:
    """Merge overlapping split results without duplicating graph identities."""
    entities: dict[tuple[str, str], dict[str, Any]] = {}
    relationships: dict[tuple[str, str, str], dict[str, Any]] = {}

    def evidence_values(value: Any) -> list[str]:
        values = value if isinstance(value, (list, tuple)) else [value]
        return [str(item).strip() for item in values if isinstance(item, str) and item.strip()]

    for extraction in extractions:
        for entity in extraction["entities"]:
            key = (entity["id"].casefold(), entity["type"].casefold())
            entities.setdefault(key, entity)
        for relationship in extraction["relationships"]:
            key = (
                relationship["source_id"].casefold(),
                relationship["type"].casefold(),
                relationship["target_id"].casefold(),
            )
            current = relationships.get(key)
            if current is None:
                relationships[key] = relationship
                continue
            current_evidence = (current.get("properties") or {}).get("evidence")
            new_evidence = (relationship.get("properties") or {}).get("evidence")
            merged_evidence = list(dict.fromkeys([
                *evidence_values(current_evidence),
                *evidence_values(new_evidence),
            ]))
            if merged_evidence:
                properties = current.get("properties")
                if not isinstance(properties, dict):
                    properties = {}
                    current["properties"] = properties
                properties["evidence"] = merged_evidence
    return {"entities": list(entities.values()), "relationships": list(relationships.values())}


async def extract_graph_facts(
    graph_extractor: Any,
    rows: list[dict[str, Any]],
    *,
    section_title: str,
    _split_depth: int = 0,
) -> dict[str, list[dict[str, Any]]]:
    """Extract graph facts from one book section for the Neo4j indexing node."""
    text = (
        f"Book section: {section_title}\n\n"
        f"{domains.bd.rag.source.indexes.neo4j_index.domain.source_material(rows)}"
    )
    try:
        extraction = await graph_extractor.aextract_text(
            text,
            source_name="bd-book-section.md",
        )
    except Exception as error:
        source_chars = sum(len(str(row.get("content") or "")) for row in rows)
        split_batches = _split_graph_rows(rows)
        if (
            not domains.settings.chat.domain.is_length_error(error)
            or not split_batches
            or _split_depth >= 8
        ):
            raise
        logger.info(
            "[bd:neo4j] Docling Graph response exceeded its output limit; splitting %d source chars",
            source_chars,
        )
        split_results = [
            await extract_graph_facts(
                graph_extractor,
                batch,
                section_title=section_title,
                _split_depth=_split_depth + 1,
            )
            for batch in split_batches
        ]
        return _merge_graph_facts(split_results)
    return domains.bd.rag.source.indexes.neo4j_index.domain.graph_facts(extraction)


async def index_book_graph(
    graph_state: graphs.ingestion.rag_indexing.states.ProcessedBookIndexState,
    context: graphs.ingestion.rag_indexing.entities.ProcessedBookIndexDependencies,
) -> dict[str, Any]:
    rows = await domains.bd.rag.source.storage.service.read_sources(graph_state, context.storage)
    batches = domains.bd.rag.source.indexes.neo4j_index.domain.graph_batches(rows)
    all_pages = {row["processed_page_number"] for row in rows}
    chunks_remaining_by_page: dict[int, int] = {}
    for _, batch_rows in batches:
        for row in batch_rows:
            page_number = row["processed_page_number"]
            chunks_remaining_by_page[page_number] = chunks_remaining_by_page.get(page_number, 0) + 1
    completed_pages = all_pages - chunks_remaining_by_page.keys()
    pages_completed = len(completed_pages)
    pages_total = len(all_pages)
    node_write_batches_completed = 0
    node_write_batches_total = 0
    relationship_write_batches_completed = 0
    relationship_write_batches_total = 0
    await context.progress_callback(
        "neo4j",
        "running",
        {
            "neo4j_stage": "extracting",
            "neo4j_stage_completed": pages_completed,
            "neo4j_stage_total": pages_total,
            "graph_batches_completed": 0,
            "graph_batches_total": len(batches),
            "neo4j_pages_completed": pages_completed,
        },
    )
    extraction_semaphore = asyncio.Semaphore(domains.bd.rag.ai.params.GRAPH_EXTRACTION_CONCURRENCY)
    progress_lock = asyncio.Lock()
    completed_batches = 0
    active_llm_calls = 0

    async def extract_batch(
        section_title: str,
        batch_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        nonlocal active_llm_calls, completed_batches, pages_completed
        extraction: dict[str, list[dict[str, Any]]] | None = None
        page_numbers = sorted({row["processed_page_number"] for row in batch_rows})
        retry_attempts = domains.bd.rag.ai.params.GRAPH_EXTRACTION_RETRY_ATTEMPTS
        for attempt in range(1, retry_attempts + 1):
            retry_error: Exception | None = None
            async with extraction_semaphore:
                if await context.cancel_check():
                    raise RuntimeError("processed-book retrieval indexing was cancelled")
                registered = False
                try:
                    async with progress_lock:
                        active_llm_calls += 1
                        registered = True
                        await context.progress_callback(
                            "neo4j",
                            "running",
                            {"neo4j_llm_calls_active": active_llm_calls},
                        )
                    with domains.settings.llm_counter.service.llm_context(
                        ns = "bd",
                        run_id = domains.bd.rag.ai.keys.usage_run_id(
                            graph_state["book_id"],
                            graph_state["agentic_run_id"],
                        ),
                        node_id = "neo4j",
                        stage = "agentic_rag",
                    ):
                        extraction = await extract_graph_facts(
                            context.graph_extractor,
                            batch_rows,
                            section_title = section_title,
                        )
                except Exception as error:
                    if (
                        attempt >= retry_attempts
                        or not domains.settings.chat.domain.is_transient_provider_error(error)
                    ):
                        raise
                    retry_error = error
                finally:
                    if registered:
                        async with progress_lock:
                            active_llm_calls -= 1
                            await context.progress_callback(
                                "neo4j",
                                "running",
                                {"neo4j_llm_calls_active": active_llm_calls},
                            )

            if retry_error is None:
                break
            delay_cap = min(
                domains.bd.rag.ai.params.GRAPH_EXTRACTION_RETRY_INITIAL_INTERVAL_S * 2 ** (attempt - 1),
                domains.bd.rag.ai.params.GRAPH_EXTRACTION_RETRY_MAX_INTERVAL_S,
            )
            retry_delay = random.uniform(delay_cap / 2, delay_cap)
            logger.warning(
                "[bd-rag-neo4j] transient graph extraction error; retrying attempt %d/%d "
                "for pages %s in %.1f seconds (%s)",
                attempt + 1,
                retry_attempts,
                page_numbers,
                retry_delay,
                type(retry_error).__name__,
            )
            retry_deadline = asyncio.get_running_loop().time() + retry_delay
            while True:
                if await context.cancel_check():
                    raise RuntimeError("processed-book retrieval indexing was cancelled") from retry_error
                remaining = retry_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(1.0, remaining))

        if extraction is None:
            raise RuntimeError("Neo4j graph extraction completed without a result")

        async with progress_lock:
            for row in batch_rows:
                page_number = row["processed_page_number"]
                chunks_remaining_by_page[page_number] -= 1
                if chunks_remaining_by_page[page_number] == 0 and page_number not in completed_pages:
                    completed_pages.add(page_number)
                    pages_completed += 1
            completed_batches += 1
            await context.progress_callback(
                "neo4j",
                "running",
                {
                    "neo4j_stage": "extracting",
                    "neo4j_stage_completed": pages_completed,
                    "neo4j_stage_total": pages_total,
                    "graph_batches_completed": completed_batches,
                    "graph_batches_total": len(batches),
                    "neo4j_pages_completed": pages_completed,
                    "neo4j_llm_calls_active": active_llm_calls,
                },
            )
        return {"section_title": section_title, "rows": batch_rows, "extraction": extraction}

    extractions = await _gather_cancel_on_failure(
        extract_batch(section_title, batch_rows)
        for section_title, batch_rows in batches
    )
    await context.progress_callback(
        "neo4j",
        "running",
        {
            "neo4j_stage": "building_graph",
            "neo4j_stage_completed": 0,
            "neo4j_stage_total": 1,
            "graph_batches_completed": len(batches),
            "graph_batches_total": len(batches),
            "neo4j_pages_completed": pages_completed,
        },
    )
    nodes, edges = domains.bd.rag.source.indexes.neo4j_index.domain.build_graph(
        rows,
        extractions,
        book_id = graph_state["book_id"],
        agentic_run_id = graph_state["agentic_run_id"],
        revision = graph_state["processed_book_revision"],
    )
    await context.progress_callback(
        "neo4j",
        "running",
        {
            "neo4j_stage": "building_graph",
            "neo4j_stage_completed": 1,
            "neo4j_stage_total": 1,
        },
    )
    if await context.cancel_check():
        raise RuntimeError("processed-book retrieval indexing was cancelled")
    await context.progress_callback(
        "neo4j",
        "running",
        {
            "neo4j_stage": "clearing_graph",
            "neo4j_stage_completed": 0,
            "neo4j_stage_total": 1,
        },
    )
    await domains.bd.rag.source.indexes.neo4j_index.service.delete_graph(
        graph_state["book_id"],
        graph_state["agentic_run_id"],
        graph_state["processed_book_revision"],
    )
    await context.progress_callback(
        "neo4j",
        "running",
        {
            "neo4j_stage": "clearing_graph",
            "neo4j_stage_completed": 1,
            "neo4j_stage_total": 1,
        },
    )

    async def report_write_progress(stage: str, completed: int, total: int) -> None:
        nonlocal node_write_batches_completed, node_write_batches_total
        nonlocal relationship_write_batches_completed, relationship_write_batches_total
        details = {
            "neo4j_stage": stage,
            "neo4j_stage_completed": completed,
            "neo4j_stage_total": total,
        }
        if stage == "writing_nodes":
            node_write_batches_completed = completed
            node_write_batches_total = total
            details.update(
                neo4j_node_write_batches_completed=completed,
                neo4j_node_write_batches_total=total,
            )
        elif stage == "writing_relationships":
            relationship_write_batches_completed = completed
            relationship_write_batches_total = total
            details.update(
                neo4j_relationship_write_batches_completed=completed,
                neo4j_relationship_write_batches_total=total,
            )
        await context.progress_callback("neo4j", "running", details)

    node_count, relationship_count = await domains.bd.rag.source.indexes.neo4j_index.service.write_graph(
        nodes,
        edges,
        book_id = graph_state["book_id"],
        agentic_run_id = graph_state["agentic_run_id"],
        revision = graph_state["processed_book_revision"],
        progress_callback = report_write_progress,
        cancel_check = context.cancel_check,
    )
    await context.progress_callback(
        "neo4j",
        "running",
        {
            "neo4j_stage": "complete",
            "neo4j_stage_completed": 1,
            "neo4j_stage_total": 1,
            "neo4j_node_write_batches_completed": node_write_batches_completed,
            "neo4j_node_write_batches_total": node_write_batches_total,
            "neo4j_relationship_write_batches_completed": relationship_write_batches_completed,
            "neo4j_relationship_write_batches_total": relationship_write_batches_total,
        },
    )
    return {
        "neo4j_node_count": node_count,
        "neo4j_relationship_count": relationship_count,
        "neo4j_pages_completed": pages_completed,
        "neo4j_stage": "complete",
        "neo4j_stage_completed": 1,
        "neo4j_stage_total": 1,
        "neo4j_node_write_batches_completed": node_write_batches_completed,
        "neo4j_node_write_batches_total": node_write_batches_total,
        "neo4j_relationship_write_batches_completed": relationship_write_batches_completed,
        "neo4j_relationship_write_batches_total": relationship_write_batches_total,
    }


async def _gather_cancel_on_failure(awaitables: Iterable[Coroutine[Any, Any, Any]]) -> list[Any]:
    tasks = [asyncio.create_task(awaitable) for awaitable in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions = True)
        raise
