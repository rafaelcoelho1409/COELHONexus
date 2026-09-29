from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ContentType = Literal[
    "text/markdown",
    "application/json",
    "text/plain",
    "application/octet-stream",
]


@dataclass
class ManifestEntry:
    idx:   int
    slug:  str
    url:   str
    tier:  str
    bytes: int
    title: str = ""
    key:   str = ""        # MinIO key — populated once written
    # Per-section `Source: <url>` line found inside a split monolith page
    # (post.domain.split_by_source_markers) — `url` above stays the ORIGINAL
    # bundle URL shared by every split sibling, so this is the only field
    # that actually distinguishes them. Empty when no per-section source was
    # found (H1/H2 fallback split, or a page that was never split).
    source_path: str = ""
    # Tier 2 (llms.txt) enrichment — the enclosing `##` file-list section and
    # the link's optional `: notes` suffix (AnswerDotAI spec v2). Empty for
    # all other tiers; downstream must fall back to URL-path grouping when "".
    section: str = ""
    notes: str = ""
    # Tier 3 sitemap `<lastmod>` for this URL ("" when the index carries
    # none). Freshness signal for the explorer + future incremental
    # re-ingest; never a correctness input.
    lastmod: str = ""
    # Tier 4 fragment anchors collapsed onto this base page (objects.inv
    # `std:label` entities, page-split sub-slices). The base URL is the
    # canonical identity (fragments never are); anchors stay queryable
    # metadata instead of separate near-duplicate documents.
    anchors: list = field(default_factory = list)
