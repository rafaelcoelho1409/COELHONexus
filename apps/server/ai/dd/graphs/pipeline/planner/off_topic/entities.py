"""What the steps of the off_topic subgraph hand to each other."""
from __future__ import annotations

from typing import NotRequired, TypedDict


class Verdict(TypedDict):
    """The judge's verdict on one distinct page text (`digest`), in round 1 or in the second look at a drop."""

    digest: str
    keep: bool
    round: int
    error: NotRequired[str]


class JudgeInput(TypedDict):
    """What one `judge_page` branch is sent: the digest of the page text, a page that has it, and the framework context."""

    digest: str
    key: str
    round: int
    framework_slug: str
    framework_name: str
    category: str
    summary: str
    total: int      # distinct texts judged in round 1, for the progress event
    deduped: int    # pages that shared a text with another
