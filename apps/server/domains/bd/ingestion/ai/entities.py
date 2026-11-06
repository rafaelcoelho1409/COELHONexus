"""Entities of Book Distiller's AI ingestion workflow."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class SpecialistContext:
    """What a repair specialist's tools need for one work item, passed with the call as the agent's runtime context.

    The tools only call what it holds: the patch check is the same one the final gate runs, and the book lookups read
    the whole book's source pages."""

    # (issue_id, target_text, replacement) -> what the application would do with that patch.
    check_patch: Callable[[str, str, str], str]
    # (variants) -> how often each spelling occurs in the whole book.
    term_counts: Callable[[list[str]], Awaitable[str]]
    # (page_number, lines) -> the last lines of the previous page and the first lines of the next.
    page_edges: Callable[[int, int], Awaitable[str]]
