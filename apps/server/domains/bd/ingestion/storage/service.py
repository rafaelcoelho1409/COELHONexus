"""A book in MinIO: the uploaded source, manifest, one markdown object per content unit, structured output and figures.
Every function takes the storage adapter (`infra.minio.service.get_storage()`) so a test hands it an in-memory one."""
from __future__ import annotations
import domains, infra
from . import domain, keys, params

import asyncio
import json


async def write_source(storage: infra.minio.service.MinIOStorage, book_id: str, data: bytes, source_format: str = "pdf") -> None:
    content_type = params.EPUB_TYPE if source_format == "epub" else params.PDF_TYPE
    await storage.write(keys.source_key(book_id, source_format), data, content_type = content_type)


async def read_source(storage: infra.minio.service.MinIOStorage, book_id: str, source_format: str = "pdf") -> bytes:
    return await storage.read_bytes(keys.source_key(book_id, source_format))


async def write_manifest(storage: infra.minio.service.MinIOStorage, manifest: dict) -> None:
    await storage.write(
        keys.manifest_key(manifest["book_id"]),
        json.dumps(manifest, separators = params.MANIFEST_JSON_SEPARATORS),
        content_type = params.JSON_TYPE,
    )


async def read_manifest(storage: infra.minio.service.MinIOStorage, book_id: str) -> dict | None:
    """The book's manifest, or None when it has none (never uploaded, or wiped)."""
    try:
        raw = await storage.read_text(keys.manifest_key(book_id))
    except Exception:  # noqa: BLE001 — a missing object is the answer "no such book"
        return None
    return domain.parse_json(raw, None)


async def write_page(storage: infra.minio.service.MinIOStorage, book_id: str, page_no: int, markdown: str) -> str:
    key = keys.page_key(book_id, page_no)
    await storage.write(key, markdown, content_type = params.MARKDOWN_TYPE)
    return key


async def read_page(storage: infra.minio.service.MinIOStorage, book_id: str, page_no: int) -> str | None:
    try:
        return await storage.read_text(keys.page_key(book_id, page_no))
    except Exception:  # noqa: BLE001 — a page not extracted yet is not an error
        return None


async def write_structured(storage: infra.minio.service.MinIOStorage, book_id: str, first: int, last: int, structured: dict) -> None:
    await storage.write(
        keys.structured_key(book_id, first, last),
        json.dumps(structured, separators = params.MANIFEST_JSON_SEPARATORS),
        content_type = params.JSON_TYPE,
    )


async def write_figures(storage: infra.minio.service.MinIOStorage, book_id: str, figures) -> None:
    await asyncio.gather(*(
        storage.write(keys.figure_key(book_id, figure.name), figure.png,
                      content_type = domains.bd.ingestion.extract.params.FIGURE_CONTENT_TYPE)
        for figure in figures
    ))


async def read_figure(storage: infra.minio.service.MinIOStorage, book_id: str, name: str) -> bytes | None:
    try:
        return await storage.read_bytes(keys.figure_key(book_id, name))
    except Exception:  # noqa: BLE001 — an unknown figure is a 404 for the caller
        return None


async def list_library(storage: infra.minio.service.MinIOStorage) -> list[dict]:
    """Every book as a library row, the most recently touched first."""
    book_ids = await storage.list_subfolders(keys.books_prefix())
    gate = asyncio.Semaphore(params.LIBRARY_READ_CONCURRENCY)

    async def _row(book_id: str) -> dict | None:
        async with gate:
            manifest = await read_manifest(storage, book_id)
        return domain.library_row(manifest) if manifest else None

    rows = [row for row in await asyncio.gather(*(_row(book_id) for book_id in book_ids)) if row]
    return domain.newest_first(rows)


async def wipe_book(storage: infra.minio.service.MinIOStorage, book_id: str) -> int:
    """Delete everything of the book; returns how many objects went."""
    return await storage.delete_prefix(keys.book_prefix(book_id))
