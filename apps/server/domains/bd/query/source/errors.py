"""Query validation errors for BD retrieval stores."""
from __future__ import annotations


class QueryNotAllowed(Exception):
    """Raised when a raw query is invalid or outside the read-only surface."""
