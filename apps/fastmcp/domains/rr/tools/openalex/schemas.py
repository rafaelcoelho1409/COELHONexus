"""Pydantic boundary schemas for the OpenAlex tool.

Two boundaries (mirrors arxiv/schemas.py + semantic_scholar/schemas.py
shape):
  * SearchInput — what the LLM sends to the tool (validated by FastMCP).
  * Paper       — what the tool returns (consumed by the agent / stores).

Per docs/CODE-CONVENTIONS.md §2: Pydantic ONLY at the boundary. The Paper
shape here is SOURCE-SPECIFIC — it surfaces OpenAlex-unique fields
(open-access status/URL, topic names, cross-source external IDs) rather
than copying arxiv's or S2's shape. The agent normalizes across sources
at the Neo4j-ingest boundary, not in the tool.
"""
from __future__ import annotations
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class SearchInput(BaseModel):
    """Search OpenAlex for works matching a query.

    OpenAlex's `search` param does a full-text match across title,
    abstract, and fulltext (when indexed) — broader recall than arxiv's
    title/abstract-only search, at the cost of some off-topic noise the
    caller should expect and filter downstream (same trade-off RR already
    makes for HN).
    """

    query: str = Field(
        ...,
        min_length=1,
        description="Free-text query — matched across title, abstract, and fulltext.",
    )
    n_max: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Max works to return (1-100; OpenAlex's per_page cap is 200).",
    )
    year_min: int | None = Field(
        default=None,
        ge=1900,
        description="Earliest publication year (inclusive). Combine with year_max.",
    )
    year_max: int | None = Field(
        default=None,
        ge=1900,
        description="Latest publication year (inclusive). Combine with year_min.",
    )
    open_access_only: bool = Field(
        default=False,
        description="Restrict to works OpenAlex marks as open access (has a free-to-read copy).",
    )


class Paper(BaseModel):
    """One OpenAlex work. SOURCE-SPECIFIC — surfaces OpenAlex-unique fields
    (open-access URL, topic names, cross-source external IDs) the agent
    uses for radar signal scoring and cross-source dedup."""

    openalex_id: str = Field(
        description="OpenAlex work ID, short form (e.g. 'W2741809807') — stable, opaque identifier."
    )
    title: str
    abstract: str | None = Field(
        default=None,
        description=(
            "Reconstructed from OpenAlex's `abstract_inverted_index` "
            "(OpenAlex doesn't store plain-text abstracts) — see "
            "domain.reconstruct_abstract. None when OpenAlex has no "
            "abstract for this work (common for older/paywalled entries)."
        ),
    )
    authors: list[str] = Field(default_factory=list)
    publication_year: int | None = None
    publication_date: date | None = None
    cited_by_count: int = 0
    work_type: str | None = Field(
        default=None,
        description="OpenAlex's `type` field, e.g. 'article', 'preprint', 'book-chapter'.",
    )
    is_open_access: bool = False
    open_access_pdf: str | None = Field(
        default=None,
        description="Direct URL to a free-to-read copy when OpenAlex has one.",
    )
    topics: list[str] = Field(
        default_factory=list,
        description="Top OpenAlex topic/concept names — analogous to S2's fields_of_study.",
    )
    external_ids: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Cross-source IDs from OpenAlex's `ids` object — typically "
            "doi, mag, pmid, pmcid (bare values, URL prefixes stripped). "
            "No native ArXiv ID field; dedup against the arxiv tool falls "
            "back to title/DOI matching downstream."
        ),
    )
    source: Literal["openalex"] = "openalex"
