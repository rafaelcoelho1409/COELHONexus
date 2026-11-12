"""What the steps of the sawc subgraph hand to each other."""
from __future__ import annotations

import typing


class WriteTask(typing.TypedDict):
    """One section for a writer to write (or write again)."""

    sid: str
    budget: typing.NotRequired[int]        # vault characters in the writer's request (a late retry uses a lighter bank)


class WriteResult(typing.TypedDict):
    sid: str
    attempts: int
    placeholder: bool


class ExtendResult(typing.TypedDict):
    sid: str
    added: int
    outcome: str                    # 'ok' | 'failed' (every call raised: worth a salvage pass) | 'empty' (answered, nothing valid)
