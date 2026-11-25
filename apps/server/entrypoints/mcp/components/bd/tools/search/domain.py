"""Pure scope and query validation for BD Search MCP."""
from __future__ import annotations

from . import patterns


def safe_segment(value: str) -> bool:
    return bool(patterns._SAFE_SEGMENT.fullmatch(value))


def normalize_query(value: str, max_chars: int) -> str:
    return " ".join(value.split())[:max_chars].strip()


def book_is_allowed(book_id: str, allowed_book_ids: frozenset[str]) -> bool:
    return safe_segment(book_id) and book_id in allowed_book_ids


def token_book_ids(claims: dict) -> frozenset[str]:
    """Read the explicit book scope issued by the trusted authorization server."""
    values = claims.get("bd_book_ids")
    if not isinstance(values, list):
        bd_claim = claims.get("bd")
        values = bd_claim.get("book_ids") if isinstance(bd_claim, dict) else None
    if not isinstance(values, list):
        return frozenset()
    return frozenset(
        value for value in values
        if isinstance(value, str) and safe_segment(value)
    )
