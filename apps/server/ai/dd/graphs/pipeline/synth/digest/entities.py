"""What the steps of the digest subgraph hand to each other."""
from __future__ import annotations

from typing import NotRequired, Optional, TypedDict


class DigestItem(TypedDict):
    """One unit of routing work: a source page, or one chunk of a page with many code blocks."""

    id: str                  # `<source key>#<chunk index>`
    key: str
    chunk: int
    n_chunks: int
    digest: str              # identity of the stored result (prompt version, outline, source, chunk text)


class DigestResult(TypedDict):
    """The outcome of one item: its digest, or why it has none."""

    id: str
    key: str
    chunk: int
    digest: Optional[dict]
    error: NotRequired[str]
