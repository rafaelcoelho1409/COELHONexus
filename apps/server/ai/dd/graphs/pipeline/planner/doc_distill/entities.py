"""What the steps of the doc_distill subgraph hand to each other."""
from __future__ import annotations

import typing


class DistillResult(typing.TypedDict):
    """The outcome for one page: its distillate, or why it has none; `fallback` marks a deterministic stand-in."""

    key: str
    distillate: dict | None
    fallback: bool
    reason: typing.NotRequired[str]


class DistillInput(typing.TypedDict):
    """What one `distill_page` branch is sent."""

    key: str
    digest: str
    framework_slug: str
    total: int
