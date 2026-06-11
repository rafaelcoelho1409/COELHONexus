"""Pydantic boundary schemas for the HN tool.

Two boundaries:
  * SearchInput — what the LLM sends (validated by FastMCP).
  * Hit         — what the tool returns (one HN post, Algolia term).

Source-specific shape (NOT a copy of arxiv.Paper) — surfaces HN-unique fields
(`points`, `num_comments`, `story_text`) and the extracted `arxiv_id` which
enables cross-source dedup with the arxiv and huggingface_daily_papers tools.
"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


SortBy = Literal["relevance", "date"]


class SearchInput(BaseModel):
    """Search Hacker News via Algolia.

    Defaults to `tags=["story"]` (top-level posts) — comments are usually too
    granular for radar digests. Use server-side `numericFilters` (`min_points`,
    `min_num_comments`, `since`) to focus on high-traction posts.
    """

    query: str = Field(
        ...,
        min_length=1,
        description="Free-text query matched against title + URL + author.",
    )
    n_max: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Cap on returned hits (1-100; Algolia hitsPerPage cap is 1000).",
    )
    tags: list[str] | None = Field(
        default=None,
        description=(
            "HN tags to include. Defaults to ['story']. Valid: 'story', "
            "'comment', 'poll', 'pollopt', 'show_hn', 'ask_hn', 'front_page'. "
            "Multiple tags AND-combined; unknown tags are silently ignored by Algolia."
        ),
    )
    min_points: int | None = Field(
        default=None,
        ge=0,
        description="Drop hits with fewer than this many HN upvotes.",
    )
    min_num_comments: int | None = Field(
        default=None,
        ge=0,
        description="Drop hits with fewer than this many comments (discussion depth).",
    )
    since: date | None = Field(
        default=None,
        description=(
            "Earliest creation date (UTC) for hits. Mapped to Algolia's "
            "`created_at_i>=<timestamp>` numericFilter."
        ),
    )
    sort_by: SortBy = Field(
        default="relevance",
        description=(
            "Algolia endpoint to call: 'relevance' (/search; ranked by HN "
            "interest score) or 'date' (/search_by_date; newest first)."
        ),
    )


class Hit(BaseModel):
    """One Hacker News post. SOURCE-SPECIFIC — surfaces HN's traction signals
    (points, num_comments) and the extracted `arxiv_id` for cross-source
    dedup with the arxiv and huggingface_daily_papers tools."""

    hn_id: str = Field(description="HN item ID (Algolia objectID).")
    title: str
    url: str | None = Field(
        default=None,
        description="External link the story points at (None for Ask HN / self-posts).",
    )
    author: str
    points: int = Field(
        default=0,
        description=(
            "HN upvotes — the killer traction signal. Strong input for the "
            "radar's `signal_score.cross_tier_buzz` term."
        ),
    )
    num_comments: int = Field(
        default=0,
        description="Discussion-thread comment count.",
    )
    created_at: datetime | None = None
    hn_url: str = Field(description="HN discussion-page URL (news.ycombinator.com/item?id=...).")
    arxiv_id: str | None = Field(
        default=None,
        description=(
            "Extracted from `url` when it's an arxiv.org/abs/<id> or "
            "huggingface.co/papers/<id> link — enables cross-source dedup "
            "with the arxiv and huggingface_daily_papers tools."
        ),
    )
    story_text: str | None = Field(
        default=None,
        description="Self-post body (Ask HN / Show HN). None for link posts.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Algolia tags: 'story', 'show_hn', 'ask_hn', 'author_<name>', ...",
    )
    source: Literal["hn"] = "hn"
