"""State of the digest subgraph."""
from __future__ import annotations

import operator
from typing import Annotated, NotRequired, Optional, TypedDict


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


class DigestState(TypedDict, total=False):
    framework_slug: str
    chapter_id: str
    thread_id: str
    digest_path: Optional[str]
    digest_stats: Optional[dict]
    status: Optional[str]
    error: Optional[str]

    finished: bool                  # answered without the steps below (a failure, or the stage's blob is current)
    started: float
    manifest_hash: str
    versioned_key: str
    latest_key: str
    outline_manifest_hash: str
    chapter_title: str
    items: list[DigestItem]
    todo: list[DigestItem]          # items still to route (the first wave, then the ones that failed)
    wave: int
    results: Annotated[list[DigestResult], operator.add]
