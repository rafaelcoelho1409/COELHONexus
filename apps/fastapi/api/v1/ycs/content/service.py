"""content service — ingestion guards + client factories shared by endpoints."""
from __future__ import annotations
import domains

from fastapi import HTTPException


async def _raise_if_embedding_migration_needed(include_transcription: bool) -> None:
    """2026-09-15: gate for the embedding-migration flow
    (`domains.ycs.embedding_migration`) — blocks dispatching a NEW
    ingestion run while the corpus is split across two embedding models
    (a change was detected but not yet migrated, or a migration is
    currently in flight). Without this, new videos would land in the
    active collection under a DIFFERENT model than everything already
    there, permanently fragmenting the corpus across two incomparable
    cosine spaces.

    Metadata-only requests (`include_transcription=False`) never touch
    embeddings — skip the check entirely rather than blocking them on an
    unrelated migration.

    This SAME check also guards `api/v1/ycs/agents/router.py`'s
    `/ingest/qdrant` and `/pipeline` endpoints — every entry point that
    can write to Qdrant needs it, not just this router's (a real gap:
    `/agents/ingest/qdrant`, live in the Source tab's UI, had no gate at
    all until this was found)."""
    if not include_transcription:
        return
    mismatch = await domains.ycs.embedding_migration.service.check_migration_needed_now()
    if mismatch is not None:
        raise HTTPException(
            status_code = 423,
            detail = {
                "error": "embedding_migration_required",
                "message": (
                    f"The configured embedding model changed from "
                    f"{mismatch['from_model']!r} to {mismatch['to_model']!r} "
                    f"since the last ingestion. Start a migration "
                    f"(POST /api/v1/ycs/content/embedding-migration/start) "
                    f"before ingesting new videos, or the corpus will "
                    f"split across two incomparable embedding spaces."
                ),
                **mismatch,
            },
        )


def _build_qdrant():
    import os
    from qdrant_client import AsyncQdrantClient
    qdrant_api_key = os.environ.get("QDRANT_API_KEY")
    return AsyncQdrantClient(
        url     = os.environ.get("QDRANT_URL", "http://localhost:6333"),
        port    = int(os.environ.get("QDRANT_PORT", "6333")),
        api_key = qdrant_api_key if qdrant_api_key else None,
    )
