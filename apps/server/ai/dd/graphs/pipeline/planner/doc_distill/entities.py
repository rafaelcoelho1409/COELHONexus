"""What the steps of the doc_distill subgraph hand to each other."""
from __future__ import annotations

from typing import NotRequired, TypedDict


class DistillResult(TypedDict):
    """The outcome for one page: its distillate, or why it has none; `fallback` marks a deterministic stand-in."""

    key: str
    distillate: dict | None
    fallback: bool
    reason: NotRequired[str]


class DistillInput(TypedDict):
    """What one `distill_page` branch is sent."""

    key: str
    digest: str
    framework_slug: str
    total: int
