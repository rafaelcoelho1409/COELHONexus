"""Pydantic boundary schemas for the HuggingFace Daily Papers tool.

Two boundaries (mirrors arxiv/semantic_scholar shape):
  * SearchInput — what the LLM sends to the tool (validated by FastMCP).
  * Paper       — what the tool returns (consumed by the agent / stores).

Source-specific Paper shape (NOT a copy of arxiv.Paper or s2.Paper) — surfaces
HF-unique fields (`upvotes`, `num_comments`, `discussion_id`) plus the
`arxiv_id` which enables cross-source dedup with the arxiv tool's results.
"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class SearchInput(BaseModel):
    """Fetch HuggingFace's daily-curated papers feed.

    UNLIKE arxiv/semantic_scholar, this endpoint is CURATED — there is no
    text-query parameter. The natural axis is publication date (which day's
    curation), plus optional community-signal post-filters.
    """

    target_date: date | None = Field(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD) of the daily curation to fetch. "
            "Defaults to today (UTC) when omitted."
        ),
    )
    n_max: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Cap on returned papers (1-50; HF typically curates 10-30 per day).",
    )
    min_upvotes: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Drop papers with fewer than this many HF community upvotes. "
            "Applied AFTER the API returns — HF doesn't server-side filter."
        ),
    )


class Paper(BaseModel):
    """One HuggingFace daily paper. SOURCE-SPECIFIC — surfaces HF-unique
    signals (upvotes, num_comments, discussion_id) and the `arxiv_id` which
    enables cross-source dedup with the arxiv tool's results."""

    arxiv_id: str = Field(
        description="ArXiv identifier — used for cross-source dedup with the arxiv tool.",
    )
    title: str
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    published: date | None = Field(
        default=None,
        description="Original arxiv publication date (from HF's nested `paper.publishedAt`).",
    )
    upvotes: int = Field(
        default=0,
        description=(
            "HF community upvote count — the killer signal that pure-arxiv lacks. "
            "Use as a strong input to the radar's `signal_score.vertical_fit`."
        ),
    )
    num_comments: int = Field(
        default=0,
        description="Count of comments on the HF discussion thread.",
    )
    discussion_id: str | None = Field(
        default=None,
        description="HF discussion thread identifier (links to community conversation).",
    )
    thumbnail: str | None = Field(
        default=None,
        description="HF-hosted thumbnail image URL (when available).",
    )
    arxiv_url: str
    pdf_url: str
    hf_url: str = Field(description="HuggingFace papers-page URL.")
    source: Literal["huggingface_daily_papers"] = "huggingface_daily_papers"
