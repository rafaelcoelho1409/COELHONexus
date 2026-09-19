"""ycs/query — exceptions for the raw-DSL read-only safety guards.
"""
from __future__ import annotations


class QueryNotAllowed(Exception):
    """Raised when a raw query fails the read-only checks."""
