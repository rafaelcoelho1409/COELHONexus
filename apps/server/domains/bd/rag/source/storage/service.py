"""Read pinned processed-book source rows for retrieval-store adapters."""
from __future__ import annotations
import infra
from . import domain

from typing import Any


async def read_sources(
    state: dict[str, Any],
    storage: infra.minio.service.MinIOStorage,
) -> list[dict[str, Any]]:
    key = state.get("source_artifact_key")
    if not isinstance(key, str):
        raise TypeError("the pinned source artifact key must be a string")
    return domain.source_rows(await storage.read_text(key))
