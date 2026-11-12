"""What the steps of the order_chapters subgraph hand to each other."""
from __future__ import annotations

import typing


class OrderSample(typing.TypedDict):
    """One sequencer sample: the chapter order it produced, or why it produced none."""

    sample: int
    order: list[int] | None
    error: typing.NotRequired[str]


class SampleInput(typing.TypedDict):
    """What one `sample_order` branch is sent."""

    sample_idx: int
    framework_slug: str
    chapters: list[dict]
    composition: list[dict | None] | None
