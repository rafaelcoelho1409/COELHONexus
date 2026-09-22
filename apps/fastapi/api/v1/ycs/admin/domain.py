"""admin domain — pure listing helpers (no I/O)."""
from __future__ import annotations

import domains


def _absolutize_thumb(url: str | None) -> str:
    """Thin wrapper over content-domain helper so library listing shares Source preview's exact behavior."""
    return domains.ycs.content.domain._absolutize_thumbnail_url(url or "")


def _status_for(has_transcript: bool, has_neo4j_doc: bool) -> str:
    """3-state status: `done` = all 3 stores present; `partial` = no Neo4j; `failed` = no transcript."""
    if has_transcript and has_neo4j_doc:
        return "done"
    if has_transcript:
        return "partial"
    return "failed"
