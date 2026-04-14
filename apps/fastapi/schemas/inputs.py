from pydantic import BaseModel, ConfigDict
from typing import Literal


class LLMConfig(BaseModel):
    provider: str = "NVIDIA"
    model: str | None = None
    temperature: float | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_config = ConfigDict(extra="allow")


# =============================================================================
# YouTube Content Extraction Requests (POST payloads)
# =============================================================================
class SearchRequest(BaseModel):
    """
    Search YouTube videos by query. Returns search results (no ES indexing).

    All filters use yt-dlp post-processing via --match-filter or --dateafter/--datebefore.

    Numeric filters use optional comparison (>=?) to include videos where field is missing.

    String filters support operators:
    - Exact match: "Python Tutorial"
    - Contains (*=): "*=tutorial"
    - Starts with (^=): "^=How to"
    - Ends with ($=): "$=2026"
    - Regex (~=): "~=(?i)python"  (case-insensitive)
    - Negation (!): "!*=tutorial" (does not contain)

    Date format: YYYYMMDD or relative like "today-2weeks", "now-1month", "yesterday"
    """
    query: str
    max_results: int = 10

    # Sort order (ytsearchdate prefix)
    sort_by_date: bool = False

    # Duration filter (--match-filter duration)
    duration: Literal[
        "Under 4 minutes",
        "4 - 20 minutes",
        "Over 20 minutes"
    ] | None = None
    # Exact duration range in seconds (overrides duration preset)
    duration_min: int | None = None
    duration_max: int | None = None

    # Date filters (--dateafter/--datebefore)
    # Format: YYYYMMDD or relative like "today-2weeks", "now-1month"
    date_after: str | None = None
    date_before: str | None = None

    # View count filters (--match-filter view_count>=?N)
    min_views: int | None = None
    max_views: int | None = None

    # Like count filter (--match-filter like_count>=?N)
    min_likes: int | None = None

    # Live status filter (--match-filter)
    # True = only live, False = exclude live, None = all
    is_live: bool | None = None
    # More granular live_status values (from yt-dlp docs line 1358)
    live_status: Literal[
        "not_live",      # Regular video
        "is_live",       # Currently live
        "is_upcoming",   # Scheduled/premiering
        "was_live",      # Was live, now VOD
        "post_live"      # Was live, VOD not yet processed
    ] | None = None

    # Availability filter (--match-filter availability)
    # Values from yt-dlp docs line 1362
    availability: Literal[
        "public",
        "unlisted",
        "private",
        "premium_only",
        "subscriber_only",
        "needs_auth"
    ] | None = None

    # Age limit filter (--age-limit YEARS)
    age_limit: int | None = None

    # String filters (--match-filter with operators)
    # Supports: exact, *=contains, ^=starts_with, $=ends_with, ~=regex
    title_contains: str | None = None
    description_contains: str | None = None
    channel_name: str | None = None


class VideosRequest(BaseModel):
    """Fetch specific videos by ID."""
    video_ids: list[str]
    include_transcription: bool = True
    transcription_languages: list[str] | None = None


class ChannelRequest(BaseModel):
    """Fetch videos from a YouTube channel."""
    channel_id: str  # Can be @handle or channel ID
    max_results: int = 10  # 0 = all videos
    include_transcription: bool = True
    transcription_languages: list[str] | None = None


class PlaylistRequest(BaseModel):
    """Fetch videos from a YouTube playlist."""
    playlist_id: str
    max_results: int = 10  # 0 = all videos
    include_transcription: bool = True
    transcription_languages: list[str] | None = None


# =============================================================================
# Agentic RAG Requests
# =============================================================================
class RAGSearchRequest(BaseModel):
    """
    Search YouTube content using Adaptive Agentic RAG.

    Modes (auto-detected by classifier, or forced via force_mode):
    - fast: simple questions → direct LLM answer, no retrieval (<2s)
    - standard: factual questions → full RAG pipeline with citations (15-60s)
    - deep: analytical questions → multi-agent research with synthesis (30-120s)
    """
    question: str
    thread_id: str = "default"
    max_retries: int = 3
    force_mode: Literal["fast", "standard", "deep"] | None = None
    channel_ids: list[str] | None = None  # Scope to specific channels (auto-detected if not provided)
