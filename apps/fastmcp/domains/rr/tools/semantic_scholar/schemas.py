"""Pydantic boundary schemas for the Semantic Scholar tool.

Two boundaries (mirrors arxiv/schemas.py shape):
  * SearchInput — what the LLM sends to the tool (validated by FastMCP).
  * Paper       — what the tool returns (consumed by the agent / stores).

Per docs/CODE-CONVENTIONS.md §2: Pydantic ONLY at the boundary. The Paper
shape here is SOURCE-SPECIFIC (not a copy of arxiv.Paper) — it surfaces
S2-unique fields (tldr · influential_citation_count · external_ids ·
open_access_pdf) that arxiv simply doesn't have. The agent normalizes across
sources at the Neo4j-ingest boundary, not in the tool.
"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class SearchInput(BaseModel):
    """Search Semantic Scholar for papers matching a query.

    Query syntax (per S2):
        - free text (AND across terms by default)
        - "phrase"  → exact phrase
        - +term     → must-include
        - -term     → must-exclude
        - a | b     → a OR b

    Filters apply server-side — push every filter through the API rather than
    post-filtering downstream.
    """

    query: str = Field(
        ...,
        min_length=1,
        description="Free-text query. See S2 query syntax for boolean/phrase ops.",
    )
    n_max: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Max papers to return (1-100; S2 API cap is 100/page).",
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
    fields_of_study: list[str] | None = Field(
        default=None,
        description=(
            "Filter by S2 fields of study, e.g. ['Computer Science', 'Mathematics']. "
            "S2 quietly ignores unknown values — see keys.FIELDS_OF_STUDY for the "
            "canonical list."
        ),
    )
    min_citation_count: int | None = Field(
        default=None,
        ge=0,
        description="Drop papers with fewer than this many citations.",
    )
    venue_filter: list[str] | None = Field(
        default=None,
        description="Filter by venue name, e.g. ['NeurIPS', 'ICML', 'ICLR'].",
    )


class Paper(BaseModel):
    """One Semantic Scholar paper. SOURCE-SPECIFIC — surfaces S2-unique fields
    (tldr · influential_citation_count · external_ids) the agent uses for radar
    signal scoring and cross-source dedup with arxiv."""

    s2_id: str = Field(description="S2 paperId — stable, opaque identifier.")
    title: str
    abstract: str | None = None
    tldr: str | None = Field(
        default=None,
        description=(
            "S2's auto-generated 1-sentence summary. When present, the agent's "
            "distillation step can use it directly and skip an LLM call."
        ),
    )
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    publication_date: date | None = Field(
        default=None,
        description=(
            "Full date (YYYY-MM-DD) when S2 has it. Partial dates "
            "('YYYY-MM' / 'YYYY') yield None; the `year` field still carries the year."
        ),
    )
    citation_count: int = 0
    influential_citation_count: int = Field(
        default=0,
        description=(
            "S2's 'meaningfully cited' count — citations that substantively use the "
            "work. Better quality signal than raw citation_count."
        ),
    )
    reference_count: int = 0
    venue: str | None = None
    fields_of_study: list[str] = Field(default_factory=list)
    open_access_pdf: str | None = Field(
        default=None,
        description="Direct PDF URL when S2 has an open-access copy.",
    )
    external_ids: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Cross-source IDs — typically includes DOI, ArXiv, PubMed, MAG, CorpusId. "
            "The radar uses `ArXiv` for dedup against the arxiv tool's results."
        ),
    )
    source: Literal["semantic_scholar"] = "semantic_scholar"
