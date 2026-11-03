"""Book sources, manifests and extraction artifacts in the configured MinIO bucket."""
from __future__ import annotations
import domains, infra
from . import domain, keys, params

import asyncio
import json
import time


async def write_source(storage: infra.minio.service.MinIOStorage, book_id: str, data: bytes, source_format: str = "pdf") -> None:
    content_type = params.EPUB_TYPE if source_format == "epub" else params.PDF_TYPE
    await storage.write(keys.source_key(book_id, source_format), data, content_type = content_type)


async def read_source(storage: infra.minio.service.MinIOStorage, book_id: str, source_format: str = "pdf") -> bytes:
    for key in (
        keys.source_key(book_id, source_format),
        keys.previous_source_key(book_id, source_format),
        keys.legacy_source_key(book_id, source_format),
    ):
        try:
            return await storage.read_bytes(key)
        except Exception:  # noqa: BLE001 — try each supported layout during migration
            pass
    raise FileNotFoundError(f"source for book {book_id} was not found")


async def write_manifest(storage: infra.minio.service.MinIOStorage, manifest: dict) -> None:
    await storage.write(
        keys.manifest_key(manifest["book_id"]),
        json.dumps(manifest, separators = params.MANIFEST_JSON_SEPARATORS),
        content_type = params.JSON_TYPE,
    )


async def _read_manifest_key(storage, key: str) -> dict | None:
    try:
        manifest = domain.parse_json(await storage.read_text(key), None)
        return manifest if isinstance(manifest, dict) else None
    except Exception:  # noqa: BLE001 — a missing object is the answer "no such book"
        return None


async def read_manifest(storage: infra.minio.service.MinIOStorage, book_id: str) -> dict | None:
    """Read the canonical manifest, falling back through previous layouts during migration."""
    for key in (
        keys.manifest_key(book_id),
        keys.previous_manifest_key(book_id),
        keys.legacy_manifest_key(book_id),
    ):
        manifest = await _read_manifest_key(storage, key)
        if manifest:
            return manifest
    return None


async def write_page(
    storage: infra.minio.service.MinIOStorage, book_id: str, page_no: int, markdown: str, extraction_id: str,
) -> str:
    key = keys.page_key(book_id, page_no, extraction_id)
    await storage.write(key, markdown, content_type = params.MARKDOWN_TYPE)
    return key


async def read_page(storage: infra.minio.service.MinIOStorage, book_id: str, page_no: int) -> str | None:
    manifest = await read_manifest(storage, book_id)
    entry = domain.entry_for_page(manifest or {}, page_no)
    if not entry:
        return None
    extraction_id = (manifest or {}).get("extraction_id")
    candidate_keys = [entry.get("key")]
    if extraction_id:
        candidate_keys.extend((
            keys.page_key(book_id, page_no, extraction_id),
            keys.previous_page_key(book_id, page_no, extraction_id),
        ))
    candidate_keys.append(keys.page_key(book_id, page_no))
    for key in dict.fromkeys(key for key in candidate_keys if key):
        try:
            return await storage.read_text(key)
        except Exception:  # noqa: BLE001 — try each supported layout during migration
            pass
    return None


async def write_structured(
    storage: infra.minio.service.MinIOStorage, book_id: str, first: int, last: int, structured: dict,
    extraction_id: str,
) -> None:
    await storage.write(
        keys.structured_key(book_id, first, last, extraction_id),
        json.dumps(structured, separators = params.MANIFEST_JSON_SEPARATORS),
        content_type = params.JSON_TYPE,
    )


async def write_figures(
    storage: infra.minio.service.MinIOStorage, book_id: str, figures, extraction_id: str,
) -> None:
    await asyncio.gather(*(
        storage.write(keys.figure_key(book_id, figure.name, extraction_id), figure.png,
                      content_type = domains.bd.ingestion.extract.params.FIGURE_CONTENT_TYPE)
        for figure in figures
    ))


async def read_figure(storage: infra.minio.service.MinIOStorage, book_id: str, name: str) -> bytes | None:
    manifest = await read_manifest(storage, book_id)
    extraction_id = (manifest or {}).get("extraction_id")
    candidate_keys = []
    if extraction_id:
        candidate_keys.extend((
            keys.figure_key(book_id, name, extraction_id),
            keys.previous_figure_key(book_id, name, extraction_id),
        ))
    candidate_keys.append(keys.figure_key(book_id, name))
    for key in dict.fromkeys(candidate_keys):
        try:
            return await storage.read_bytes(key)
        except Exception:  # noqa: BLE001 — try each supported layout during migration
            pass
    return None


async def read_run_metadata(storage: infra.minio.service.MinIOStorage, book_id: str, extraction_id: str) -> dict | None:
    return await _read_manifest_key(storage, keys.run_metadata_key(book_id, extraction_id)) or await _read_manifest_key(
        storage, keys.previous_run_metadata_key(book_id, extraction_id),
    )


async def update_run_metadata(
    storage: infra.minio.service.MinIOStorage, manifest: dict, status: str, now: float,
    *, attempt_id: str | None = None, error: str | None = None, migrated_from_legacy: bool = False,
) -> None:
    extraction_id = manifest.get("extraction_id")
    if not extraction_id:
        return
    record = await read_run_metadata(storage, manifest["book_id"], extraction_id) or {
        "run_id": extraction_id,
        "book_id": manifest["book_id"],
        "source_format": domain.format_from_filename(manifest.get("filename", "")) or "pdf",
        "created_at": now,
        "attempts": [],
    }
    if not isinstance(record, dict):
        record = {}
    if not isinstance(record.get("attempts"), list):
        record["attempts"] = []
    record.update(status = status, updated_at = now)
    if migrated_from_legacy:
        record["migrated_from_legacy"] = True
    if status == params.STATUS_EXTRACTING:
        record.setdefault("started_at", now)
    if status in (params.STATUS_DONE, params.STATUS_FAILED, params.STATUS_CANCELLED):
        record["finished_at"] = now
    if error is not None:
        record["error"] = error
    else:
        record.pop("error", None)
    if attempt_id:
        attempts = record.setdefault("attempts", [])
        attempt = next(
            (item for item in attempts if isinstance(item, dict) and item.get("attempt_id") == attempt_id), None,
        )
        if attempt is None:
            attempt = {"attempt_id": attempt_id, "created_at": now}
            attempts.append(attempt)
        attempt.update(status = status, updated_at = now)
        if status == params.STATUS_EXTRACTING:
            attempt.setdefault("started_at", now)
        if status in (params.STATUS_DONE, params.STATUS_FAILED, params.STATUS_CANCELLED):
            attempt["finished_at"] = now
        if error is not None:
            attempt["error"] = error
        else:
            attempt.pop("error", None)
    await storage.write(
        keys.run_metadata_key(manifest["book_id"], extraction_id),
        json.dumps(record, separators = params.MANIFEST_JSON_SEPARATORS),
        content_type = params.JSON_TYPE,
    )


async def ensure_current_layout(storage: infra.minio.service.MinIOStorage, book_id: str) -> dict | None:
    """Idempotently copy a book from an older layout into the current book-first layout."""
    canonical = await _read_manifest_key(storage, keys.manifest_key(book_id))
    if canonical:
        return canonical
    previous = await _read_manifest_key(storage, keys.previous_manifest_key(book_id))
    legacy = await _read_manifest_key(storage, keys.legacy_manifest_key(book_id))
    if not previous and not legacy:
        return None
    await migrate_legacy_book(storage, book_id)
    return await read_manifest(storage, book_id)


async def migrate_legacy_book(
    storage: infra.minio.service.MinIOStorage, book_id: str, *, dry_run: bool = False, delete_legacy: bool = False,
) -> dict | None:
    """Copy one book into `bd/books/<id>/ingestion/raw/<run-id>/`.

    Copies are idempotent and the canonical manifest is written last. Legacy objects are retained unless explicitly
    deleted after verifying every destination exists.
    """
    canonical = await _read_manifest_key(storage, keys.manifest_key(book_id))
    old_prefixes = (
        keys.previous_book_prefix(book_id),
        keys.legacy_book_prefix(book_id),
    )
    manifests = [
        (prefix, await _read_manifest_key(storage, prefix + "manifest.json"))
        for prefix in old_prefixes
    ]
    old_manifest = next((value for _, value in manifests if value), None)
    if not old_manifest:
        return canonical
    extraction_id = (
        (canonical or {}).get("extraction_id")
        or old_manifest.get("extraction_id")
        or domain.legacy_extraction_id(old_manifest)
    )
    sources = []
    source_prefixes = []
    for prefix, prefix_manifest in manifests:
        if not prefix_manifest:
            continue
        source_prefixes.append(prefix)
        sources.extend((prefix, object_key) for object_key in await storage.list(prefix))
    copies = []
    destinations = set()
    for prefix, old_key in sources:
        relative = old_key[len(prefix):]
        if relative == "manifest.json":
            continue
        destination = (
            _previous_destination(old_key, prefix, book_id)
            if prefix == keys.previous_book_prefix(book_id)
            else _legacy_destination(old_key, prefix, book_id, extraction_id)
        )
        if destination and destination not in destinations:
            copies.append((old_key, destination))
            destinations.add(destination)
    if canonical and canonical.get("extraction_id"):
        if not delete_legacy:
            return {"book_id": book_id, "extraction_id": extraction_id, "objects": 0, "status": "already_migrated"}
        destination_keys = set(await storage.list(keys.book_prefix(book_id)))
        missing = [new_key for _, new_key in copies if new_key not in destination_keys]
        if missing:
            raise RuntimeError(
                f"legacy migration verification failed for {book_id}: {len(missing)} destination objects missing",
            )
        for prefix in source_prefixes:
            await storage.delete_prefix(prefix)
        return {"book_id": book_id, "extraction_id": extraction_id, "objects": len(copies), "status": "legacy_deleted"}
    if dry_run:
        return {"book_id": book_id, "extraction_id": extraction_id, "objects": len(copies), "status": "planned"}

    if hasattr(storage, "copy_objects"):
        await storage.copy_objects(copies)
    else:
        for old_key, new_key in copies:
            await storage.copy_object(old_key, new_key)
    manifest = {**old_manifest, "extraction_id": extraction_id}
    page_keys = {old_key: new_key for old_key, new_key in copies}
    manifest["pages"] = [
        {
            **entry,
            "key": page_keys.get(entry.get("key"), keys.page_key(book_id, entry["page"], extraction_id)),
        }
        for entry in manifest.get("pages", [])
    ]
    now = time.time()
    await update_run_metadata(
        storage, manifest, manifest.get("status", params.STATUS_QUEUED), now,
        migrated_from_legacy = True,
    )
    await write_manifest(storage, manifest)

    if delete_legacy:
        destination_keys = set(await storage.list(keys.book_prefix(book_id)))
        missing = [new_key for _, new_key in copies if new_key not in destination_keys]
        if missing:
            raise RuntimeError(
                f"legacy migration verification failed for {book_id}: {len(missing)} destination objects missing",
            )
        for prefix, prefix_manifest in manifests:
            if prefix_manifest:
                await storage.delete_prefix(prefix)
    return {"book_id": book_id, "extraction_id": extraction_id, "objects": len(copies), "status": "migrated"}


def _legacy_destination(old_key: str, old_prefix: str, book_id: str, extraction_id: str) -> str:
    relative = old_key[len(old_prefix):]
    if relative in ("source.pdf", "source.epub"):
        return keys.source_key(book_id, relative.removeprefix("source."))
    if relative == "manifest.json":
        return keys.manifest_key(book_id)
    return keys.raw_prefix(book_id, extraction_id) + relative


def _previous_destination(old_key: str, old_prefix: str, book_id: str) -> str:
    relative = old_key[len(old_prefix):]
    if relative.startswith("runs/"):
        parts = relative.split("/", 2)
        if len(parts) == 3:
            _, extraction_id, artifact_path = parts
            if artifact_path.startswith("raw/"):
                return keys.raw_prefix(book_id, extraction_id) + artifact_path.removeprefix("raw/")
            if artifact_path == "run.json":
                return keys.run_metadata_key(book_id, extraction_id)
            return keys.extraction_prefix(book_id, extraction_id) + artifact_path
    if relative in ("source.pdf", "source.epub"):
        return keys.source_key(book_id, relative.removeprefix("source."))
    if relative == "manifest.json":
        return keys.manifest_key(book_id)
    return ""


async def list_library(storage: infra.minio.service.MinIOStorage) -> list[dict]:
    """Every book as a library row, the most recently touched first."""
    book_ids = set(await storage.list_subfolders(keys.books_prefix()))
    book_ids.update(await storage.list_subfolders(keys.PREVIOUS_BOOKS_PREFIX))
    book_ids.update(await storage.list_subfolders(keys.LEGACY_BOOKS_PREFIX))
    gate = asyncio.Semaphore(params.LIBRARY_READ_CONCURRENCY)

    async def _row(book_id: str) -> dict | None:
        async with gate:
            manifest = await read_manifest(storage, book_id)
        return domain.library_row(manifest) if manifest else None

    rows = [row for row in await asyncio.gather(*(_row(book_id) for book_id in book_ids)) if row]
    return domain.newest_first(rows)


async def wipe_book(storage: infra.minio.service.MinIOStorage, book_id: str) -> int:
    """Delete canonical and legacy copies of the book; returns how many objects went."""
    canonical_count = await storage.delete_prefix(keys.book_prefix(book_id))
    previous_count = await storage.delete_prefix(keys.previous_book_prefix(book_id))
    legacy_count = await storage.delete_prefix(keys.legacy_book_prefix(book_id))
    return canonical_count + previous_count + legacy_count
