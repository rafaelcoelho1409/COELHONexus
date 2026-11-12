"""What the steps of the outline subgraph hand to each other."""
from __future__ import annotations

import typing


class Candidate(typing.TypedDict):
    """An outline the outliner answered, with what the stage's validators still find wrong in it."""

    outline: dict
    issues: list[str]
