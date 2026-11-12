"""What the steps of the chapter_propose subgraph hand to each other."""
from __future__ import annotations

import typing


class Sketch(typing.TypedDict):
    """What one section analyst proposed for its section of the documentation (`proposals` is None when it failed)."""

    section: str
    n_pages: int
    proposals: list[dict] | None
    error: typing.NotRequired[str]


class Outline(typing.TypedDict):
    """One architect attempt: the chapters it proposed and the corpus's evidence score for them."""

    attempt: int
    proposals: list[dict]
    scored: dict


class SectionInput(typing.TypedDict):
    """What one `analyze_section` branch is sent."""

    section: str
    keys: list[str]
    target: int
    framework_slug: str
