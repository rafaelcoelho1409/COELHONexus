"""What the steps of the chapter_assign subgraph hand to each other."""
from __future__ import annotations

import typing


class ScoreResult(typing.TypedDict):
    """One page's chapter confidences (`scores` is None for a page with no content at all); `fallback` marks a lexical stand-in."""

    key: str
    scores: list[dict] | None
    fallback: bool
    round: int


class ScoreInput(typing.TypedDict):
    """What one `score_page` branch is sent."""

    key: str
    digest: str
    framework_slug: str
    doc_path: str
    round: int
    total: int
