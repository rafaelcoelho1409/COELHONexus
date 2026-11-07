"""What the steps of the order_chapters subgraph hand to each other."""
from __future__ import annotations

from typing import NotRequired, TypedDict


class OrderSample(TypedDict):
    """One sequencer sample: the chapter order it produced, or why it produced none."""

    sample: int
    order: list[int] | None
    error: NotRequired[str]


class SampleInput(TypedDict):
    """What one `sample_order` branch is sent."""

    sample_idx: int
    framework_slug: str
    chapters: list[dict]
    composition: list[dict | None] | None
