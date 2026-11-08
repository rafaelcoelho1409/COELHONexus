"""Pure policy for turning a page-role assessment into a conservative selection."""
from __future__ import annotations

import domains


def disposition(role: str, confidence: float, *, delivery_disposition: str, has_pictures: bool) -> tuple[str, str]:
    """Exclude only known non-learning matter with high confidence; retain ambiguous and image-only pages."""
    if delivery_disposition == "discarded_blank":
        return "exclude", "content_policy"
    if has_pictures and role == "unknown":
        return "include", "conservative_fallback"
    if (
        role in {"navigation", "legal", "noise"}
        and confidence >= domains.bd.ingestion.ai.params.CONTENT_EXCLUSION_CONFIDENCE
    ):
        return "exclude", "content_policy"
    return "include", "classifier"
