"""Full-text indexing of canonical processed-book pages."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import domains
import infra
from elasticsearch.helpers import async_streaming_bulk

from . import params


async def index_pages(
    rows: list[dict[str, Any]],
    *,
    book_id: str,
    agentic_run_id: str,
    revision: int,
    progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
) -> tuple[str, int]:
    index = infra.elasticsearch.keys.INDEX_BD_INGESTION
    schema = infra.elasticsearch.schemas.BD_INGESTION_MAPPING
    index_domain = domains.bd.ingestion.ai.retrieval_index.elasticsearch_index.domain
    fingerprint = index_domain.book_fingerprint(book_id)
    client = infra.elasticsearch.service.new_es()
    try:
        await infra.elasticsearch.service.ensure_index(client, index, schema)
        operations = [
            {
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
            }
            for row in rows
        ]
        if progress_callback is not None:
            await progress_callback(0, len(operations))

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
        async for result in async_streaming_bulk(
            client,
            guarded_operations(),
            chunk_size = params.ELASTICSEARCH_BULK_SIZE,
            raise_on_error = False,
            raise_on_exception = False,
            refresh = "wait_for",
        ):
            ok, _ = result
            succeeded += int(ok)
            failed += int(not ok)
            processed += 1
            if progress_callback is not None and (
                processed % params.ELASTICSEARCH_BULK_SIZE == 0 or processed == len(operations)
            ):
                await progress_callback(succeeded, len(operations))
        if failed or succeeded != len(operations):
            raise RuntimeError(f"Elasticsearch indexed {succeeded} of {len(operations)} processed pages")
        return index, succeeded
    finally:
        await client.close()
