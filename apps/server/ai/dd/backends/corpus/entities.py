"""Value objects of the read-only corpus backend."""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen = True, slots = True)
class CorpusDoc:
    """One page of the corpus as the agent sees it: where it lives in its virtual filesystem and what is known about it."""

    idx: int
    path: str
    key: str
    title: str
    role: str
    summary: str
    terms: tuple[str, ...]
