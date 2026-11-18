"""Index canonical processed-book pages and their BM25 passage projection."""
from __future__ import annotations
import infra
from . import domain, params
from .. import qdrant_index
from ... import chunking

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from elasticsearch.helpers import async_streaming_bulk


async def index_pages(
    rows: list[dict[str, Any]],
    *,
    book_id: str,
    agentic_run_id: str,
    revision: int,
    progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
) -> tuple[str, int, str, int]:
    index = infra.elasticsearch.keys.INDEX_BD_INGESTION
    passage_index = infra.elasticsearch.keys.INDEX_BD_RAG_PASSAGES
    schema = infra.elasticsearch.schemas.BD_INGESTION_MAPPING
    passage_schema = infra.elasticsearch.schemas.BD_RAG_PASSAGES_MAPPING
    index_domain = domain
    fingerprint = index_domain.book_fingerprint(book_id)
    client = infra.elasticsearch.service.new_es()
    try:
        await infra.elasticsearch.service.ensure_index(client, index, schema)
        await infra.elasticsearch.service.ensure_index(client, passage_index, passage_schema)
        operations: list[dict[str, Any]] = []
        operation_kinds: list[str] = []
        operation_pages: list[int] = []
        expected_operations_by_page: dict[int, int] = {}
        successful_operations_by_page: dict[int, int] = {}
        observed_operations_by_page: dict[int, int] = {}
        page_count = 0
        passage_count = 0
        for row in rows:
            page_number = int(row["processed_page_number"])
            page_count += 1
            expected_operations_by_page[page_number] = 1
            successful_operations_by_page[page_number] = 0
            observed_operations_by_page[page_number] = 0
            operations.append({
                "_op_type": "index",
                "_index": index,
                "_id": index_domain.page_document_id(
                    book_id,
                    agentic_run_id,
                    revision,
                    row["processed_page_number"],
                ),
                "_source": {
                    **{
                        key: value for key, value in row.items()
                        if key not in {"id", "content_key"}
                    },
                    "book_fingerprint": fingerprint,
                },
            })
            operation_kinds.append("page")
            operation_pages.append(page_number)
        profile_fingerprint = qdrant_index.domain.index_profile_fingerprint()
        for row in rows:
            page_number = int(row["processed_page_number"])
            documents = chunking.domain.page_passages(row.get("content", ""))
            for chunk_index, document in enumerate(documents):
                start = int(document.metadata.get("start_index", 0))
                identity = qdrant_index.domain.point_id(
                    book_id,
                    agentic_run_id,
                    revision,
                    row["processed_page_number"],
                    chunk_index,
                    document.page_content,
                    profile_fingerprint,
                )
                operations.append({
                    "_op_type": "index",
                    "_index": passage_index,
                    "_id": identity,
                    "_source": {
                        "book_id": book_id,
                        "book_fingerprint": fingerprint,
                        "extraction_run_id": row["extraction_run_id"],
                        "agentic_run_id": agentic_run_id,
                        "processed_book_revision": revision,
                        "processed_page_number": row["processed_page_number"],
                        "source_page_number": row["source_page_number"],
                        "section_path": row["section_path"],
                        "content_role": row["content_role"],
                        "content_sha256": row["content_sha256"],
                        "index_profile_fingerprint": profile_fingerprint,
                        "chunk_index": chunk_index,
                        "total_chunks": len(documents),
                        "source_char_start": start,
                        "source_char_end": start + len(document.page_content),
                        "content": document.page_content,
                    },
                })
                operation_kinds.append("passage")
                operation_pages.append(page_number)
                expected_operations_by_page[page_number] += 1
                passage_count += 1
        if progress_callback is not None:
            await progress_callback(0, page_count)

        async def guarded_operations() -> AsyncIterator[dict[str, Any]]:
            for operation_index, operation in enumerate(operations):
                if (
                    cancel_check is not None
                    and operation_index % params.ELASTICSEARCH_BULK_SIZE == 0
                    and await cancel_check()
                ):
                    raise RuntimeError("processed-book retrieval indexing was cancelled")
                yield operation

        succeeded = 0
        failed = 0
        processed = 0
        page_successes = 0
        passage_successes = 0
        completed_pages = 0
        async for result in async_streaming_bulk(
            client,
            guarded_operations(),
            chunk_size = params.ELASTICSEARCH_BULK_SIZE,
            raise_on_error = False,
            raise_on_exception = False,
            refresh = False,
        ):
            ok, _item = result
            operation_kind = operation_kinds[processed]
            page_number = operation_pages[processed]
            succeeded += int(ok)
            failed += int(not ok)
            page_successes += int(ok and operation_kind == "page")
            passage_successes += int(ok and operation_kind == "passage")
            observed_operations_by_page[page_number] += 1
            successful_operations_by_page[page_number] += int(ok)
            processed += 1
            if (
                progress_callback is not None
                and observed_operations_by_page[page_number] == expected_operations_by_page[page_number]
            ):
                if successful_operations_by_page[page_number] == expected_operations_by_page[page_number]:
                    completed_pages += 1
                if (
                    completed_pages < page_count
                    and completed_pages % params.ELASTICSEARCH_BULK_SIZE == 0
                ):
                    await progress_callback(completed_pages, page_count)
        if failed or succeeded != len(operations):
            raise RuntimeError(
                f"Elasticsearch indexed {page_successes}/{page_count} pages and "
                f"{passage_successes}/{passage_count} passages"
            )
        if operations:
            await client.indices.refresh(index = [index, passage_index])
            if progress_callback is not None:
                await progress_callback(page_count, page_count)
        return index, page_count, passage_index, passage_count
    finally:
        await client.close()


async def delete_book_revision(
    client: Any,
    *,
    book_id: str,
    agentic_run_id: str,
    revision: int,
    indexes: tuple[str, ...] | None = None,
) -> int:
    """Delete one book snapshot from canonical and passage aliases."""
    index_names = indexes or (
        infra.elasticsearch.keys.INDEX_BD_INGESTION,
        infra.elasticsearch.keys.INDEX_BD_RAG_PASSAGES,
    )
    index_domain = domain
    query = {
        "bool": {
            "filter": [
                {"term": {"book_id": book_id}},
                {"term": {"book_fingerprint": index_domain.book_fingerprint(book_id)}},
                {"term": {"agentic_run_id": agentic_run_id}},
                {"term": {"processed_book_revision": revision}},
            ],
        },
    }
    existing_indexes = [
        index_name for index_name in index_names
        if await client.indices.exists(index = index_name)
    ]
    deleted = 0
    for index_name in existing_indexes:
        result = await client.delete_by_query(
            index = index_name,
            query = query,
            conflicts = "proceed",
            refresh = False,
        )
        if result.get("timed_out") or result.get("failures") or result.get("version_conflicts", 0):
            raise RuntimeError(f"Elasticsearch could not fully clear {index_name!r}")
        deleted += int(result.get("deleted", 0))
    if existing_indexes:
        await client.indices.refresh(index = existing_indexes)
    return deleted
