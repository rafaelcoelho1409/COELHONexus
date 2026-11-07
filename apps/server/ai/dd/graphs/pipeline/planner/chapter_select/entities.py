"""What the steps of the chapter_select subgraph hand to each other."""
from __future__ import annotations

from typing import NotRequired, TypedDict


class SplitOutcome(TypedDict):
    """What the curator did with one oversize chapter: the chapters that replace it, or None (the chapter stays as it is)."""

    round: int
    pos: int
    chapters: list[dict] | None
    reason: NotRequired[str]


class CurateInput(TypedDict):
    """What one `curate_chapter` branch is sent: the chapter's position in the plan and how many parts to carve."""

    pos: int
    n_parts: int
    round: int
    framework_slug: str


class SplitPlanItem(TypedDict):
    pos: int
    n_parts: int
