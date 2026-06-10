"""Pydantic boundary schemas for the arXiv tool.

Two boundaries:
  * SearchInput — what the LLM sends to the tool (validated by FastMCP).
  * Paper       — what the tool returns (consumed by the agent / stores).

Per docs/CODE-CONVENTIONS.md §2: Pydantic ONLY at the boundary; internal
value objects go in entities.py (none needed here yet).
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


SortBy = Literal["relevance", "submittedDate", "lastUpdatedDate"]


class SearchInput(BaseModel):
    """Search arXiv for papers matching a free-text query.

    Categories filter on top of the query; multiple categories are OR-ed.
    Common categories: `cs.LG` `cs.AI` `cs.CL` `stat.ML` `math.OC`
    `q-fin.PR` `q-fin.ST` `math.PR`.
    """

    query: str = Field(
        ...,
        description="Free-text query matched against title, abstract, and authors.",
        min_length=1,
    )
    n_max: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of papers to return (1-100).",
    )
    sort_by: SortBy = Field(
        default="submittedDate",
        description=(
            "Sort order. 'submittedDate' (default) returns newest first — best for "
            "radar mode. 'relevance' is best for one-shot lookups. 'lastUpdatedDate' "
            "surfaces recent revisions."
        ),
    )
    categories: list[str] | None = Field(
        default=None,
        description=(
            "Optional arXiv category filters, e.g. ['cs.LG', 'stat.ML']. Combined "
            "with the query via AND, categories OR-ed among themselves."
        ),
    )


class Paper(BaseModel):
    """One arXiv paper. The canonical Paper shape downstream stores consume."""

    arxiv_id: str = Field(description="arXiv identifier, e.g. '2406.12345v2'.")
    title: str
    abstract: str
    authors: list[str]
    primary_category: str = Field(description="The submitter-declared primary category.")
    categories: list[str] = Field(description="All categories the paper was tagged with.")
    published: datetime
    updated: datetime
    pdf_url: str
    abs_url: str
    doi: str | None = None
    comment: str | None = Field(
        default=None,
        description="Authors' free-text comment (e.g. 'NeurIPS 2026, 12 pages').",
    )
    source: Literal["arxiv"] = "arxiv"
