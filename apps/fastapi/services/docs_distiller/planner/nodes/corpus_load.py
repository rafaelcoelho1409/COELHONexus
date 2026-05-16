"""Substep 1 — corpus_load: read ingestion's canonical manifest from MinIO.

Populates `state.raw_files` with the framework's full set of page MinIO
keys. Subsequent nodes (off_topic, dedup) filter that list down.

Raises if the ingestion manifest is missing — the planner can't run on
an un-ingested framework. The HTTP layer wraps this as a 503 so the
caller knows to ingest first.
"""
from __future__ import annotations

import logging

from services.docs_distiller.ingestion.storage_minio import (
    get_storage,
    page_key,
)
from services.docs_distiller.ingestion.store import read_framework_manifest

from ..observability.spans import traced
from ..state import PlannerState


logger = logging.getLogger(__name__)


@traced("corpus_load")
async def corpus_load(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    if not slug:
        raise ValueError("planner state missing framework_slug")

    minio = get_storage()
    manifest = await read_framework_manifest(minio, slug)
    if not manifest:
        raise RuntimeError(
            f"no finalized ingestion for {slug!r} — run ingestion first"
        )

    entries = manifest.get("entries") or []
    keys: list[str] = []
    for idx, entry in enumerate(entries):
        # The manifest entries written by ingestion's finalize step carry
        # explicit MinIO keys, but fall back to the derived key shape if
        # an older manifest predates that field.
        k = entry.get("key") or page_key(slug, idx, entry.get("slug") or "")
        keys.append(k)

    logger.info(f"[corpus_load] {slug}: {len(keys)} files from manifest")
    return {"raw_files": keys}
