"""What the steps of the sawc subgraph hand to each other."""
from __future__ import annotations

from typing import NotRequired, TypedDict


class WriteTask(TypedDict):
    """One section for a writer to write (or write again)."""

    sid: str
    budget: NotRequired[int]        # vault characters in the writer's request (a late retry uses a lighter bank)


class WriteResult(TypedDict):
    sid: str
    attempts: int
    placeholder: bool


class ExtendResult(TypedDict):
    sid: str
    added: int
    outcome: str                    # 'ok' | 'failed' (every call raised: worth a salvage pass) | 'empty' (answered, nothing valid)
