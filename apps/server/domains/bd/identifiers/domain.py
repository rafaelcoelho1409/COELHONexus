"""Pure validation for BD identifiers used as path segments."""

from __future__ import annotations

from . import patterns


def is_safe_segment(value: object) -> bool:
    """Return whether a value is a non-empty ASCII identifier path segment."""
    return (
        isinstance(value, str) and patterns.SAFE_SEGMENT_RE.fullmatch(value) is not None
    )
