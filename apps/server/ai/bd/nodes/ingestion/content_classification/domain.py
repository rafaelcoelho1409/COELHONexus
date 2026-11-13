"""Pure policy for turning a page-role assessment into a conservative selection."""
from __future__ import annotations
import domains


def excerpt(markdown: str) -> str:
    """Keep the beginning and end of a long page for the bounded classifier prompt."""
    limit = domains.bd.ingestion.ai.params.CONTENT_CLASSIFICATION_EXCERPT_CHARS
    if len(markdown) <= limit:
        return markdown
    first = int(limit * 0.75)
    return f"{markdown[:first]}\n\n[Middle omitted for bounded page classification]\n\n{markdown[-(limit - first):]}"


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
