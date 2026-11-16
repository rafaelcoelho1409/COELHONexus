"""Pure validation and summary rules for ingestion publication."""
from __future__ import annotations

import hashlib

from . import params


def alphanumeric_count(text: str) -> int:
    return sum(character.isalnum() for character in text)


def lost_alphanumeric(source: str, normalized: str) -> int:
    """Count likely content loss while allowing formatting-only normalization."""
    source_count = alphanumeric_count(source)
    lost = source_count - alphanumeric_count(normalized)
    allowed = max(params.VERIFY_LOSS_MIN_CHARS, int(source_count * params.VERIFY_LOSS_RATIO))
    return lost if lost > allowed else 0


def sha256_matches(normalized: str, expected_sha256: str) -> bool:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() == expected_sha256


def summarize(
    checked_pages: int,
    integrity_failures: list[int],
    text_loss: dict[int, int],
) -> dict[str, object]:
    return {
        "checked_pages": checked_pages,
        "integrity_failure_pages": sorted(integrity_failures),
        "text_loss_pages": [
            {"page": page, "lost_alphanumeric": lost} for page, lost in sorted(text_loss.items())
        ],
        "status": "flagged" if integrity_failures or text_loss else "passed",
    }
