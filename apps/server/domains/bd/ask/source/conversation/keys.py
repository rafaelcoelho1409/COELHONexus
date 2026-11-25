"""Redis keys for active, book-scoped BD Ask turns."""
from __future__ import annotations


ACTIVE_TURN_TTL_S = 90


def active_turn_key(book_id: str, turn_token: str) -> str:
    return f"coelhonexus:bd:ask:active:{_safe_segment(book_id)}:{_safe_segment(turn_token)}"


def active_turn_pattern(book_id: str) -> str:
    return f"coelhonexus:bd:ask:active:{_safe_segment(book_id)}:*"


def _safe_segment(value: str) -> str:
    if not value or any(
        not (character.isascii() and (character.isalnum() or character in "-_"))
        for character in value
    ):
        raise ValueError("book and turn identifiers must be safe path segments")
    return value
