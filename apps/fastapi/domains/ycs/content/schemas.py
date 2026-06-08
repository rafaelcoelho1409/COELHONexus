"""ycs/content — Pydantic models at the HTTP boundary.

`SearchRequest` and `VideoSnippet` are the request / response shapes for
`POST /api/v1/ycs/content/search`. The filter grammar is faithful to the
deprecated YouTube Content Search stack so the FastHTML form contract is
unchanged."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


# Strip + non-empty in one annotation; rejects "  ", "\n\t" at validation.
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]


# yt-dlp `live_status` enum (yt-dlp.docs L1358).
LiveStatus = Literal["not_live", "is_live", "is_upcoming", "was_live", "post_live"]

# yt-dlp `availability` enum (yt-dlp.docs L1362).
Availability = Literal[
    "public", "unlisted", "private", "premium_only", "subscriber_only", "needs_auth",
]

# UI-friendly duration preset; `domain.build_match_conditions` translates
# the three values into raw `--match-filter` expressions.
DurationPreset = Literal["Under 4 minutes", "4 - 20 minutes", "Over 20 minutes"]


class SearchRequest(BaseModel):
    """Search YouTube and return ranked snippets.

    Numeric filters use yt-dlp's `>=?N` syntax (optional comparison) so a
    missing field on an entry doesn't fail the filter. String filters
    support the operator prefixes documented in patterns.STRING_FILTER_OP_PREFIXES;
    a plain string defaults to a case-insensitive contains.
    """
    model_config = ConfigDict(extra = "forbid")

    query: NonEmptyStr
    max_results: int = Field(default = 25, ge = 1, le = 200)
    sort_by_date: bool = False

    # Duration: preset OR explicit min/max in seconds. min/max overrides preset.
    duration: DurationPreset | None = None
    duration_min: int | None = Field(default = None, ge = 0)
    duration_max: int | None = Field(default = None, ge = 0)

    # YYYYMMDD or relative ("today-2weeks", "now-1month").
    date_after:  NonEmptyStr | None = None
    date_before: NonEmptyStr | None = None

    # Engagement bounds.
    min_views: int | None = Field(default = None, ge = 0)
    max_views: int | None = Field(default = None, ge = 0)
    min_likes: int | None = Field(default = None, ge = 0)

    # Live status — `is_live` is the simple toggle; `live_status` overrides.
    is_live:     bool | None = None
    live_status: LiveStatus | None = None

    availability: Availability | None = None
    age_limit:    int | None = Field(default = None, ge = 0)

    # Operator-prefixed string filters; plain text → contains.
    title_contains:       NonEmptyStr | None = None
    description_contains: NonEmptyStr | None = None
    channel_name:         NonEmptyStr | None = None
    # Shorts toggle — when True, adds `duration>?60 & !url~='/shorts/'`
    # match conditions. The `?` operator (optional comparison) lets
    # entries without a duration value pass; the URL `/shorts/` check
    # catches anything the duration heuristic misses (per the June
    # 2026 yt-dlp research — shorts have no native `is_short` field).
    exclude_shorts: bool = False
    # Show-only-kind filter. Applied SERVER-SIDE after normalization:
    # we already classify each entry with `detect_entry_kind`, which
    # is more reliable than yt-dlp's sparse `_type` in --flat-playlist
    # mode. ytsearch: is video-focused so "channel" / "playlist" often
    # yields 0 results — that's intentional, the user picks the kind
    # they want and gets only that. None = all kinds.
    kind_filter: Literal["video", "channel", "playlist"] | None = None


class VideoSnippet(BaseModel):
    """One row in a search-result page. Fields mirror the
    `--flat-playlist --dump-single-json` projection — not full video
    metadata (extract_video covers that).

    Name kept for stability even though `kind` may be `"channel"` or
    `"playlist"` for non-video search hits. The frontend uses `kind`
    to draw a badge and to gate bulk-action routing (only `video`
    selections can go to the Videos tab, etc.)."""
    model_config = ConfigDict(extra = "forbid")

    id:              str
    kind:            str = "video"   # video | channel | playlist
    title:           str | None = None
    url:             str
    duration:        int | None = None
    duration_string: str | None = None
    view_count:      int | None = None
    like_count:      int | None = None
    channel:         str | None = None
    channel_id:      str | None = None
    channel_url:     str | None = None
    thumbnail:       str | None = None
    description:     str | None = None
    upload_date:     str | None = None
    # Unix epoch seconds — yt-dlp populates one or both for live /
    # premiere / scheduled entries where `upload_date` is missing.
    # Frontend falls back to these when rendering the release date.
    timestamp:         int | None = None
    release_timestamp: int | None = None
    live_status:     str | None = None
    availability:    str | None = None
    # For `kind` ∈ {"channel", "playlist"}: total number of videos
    # belonging to that channel/playlist. None for plain video rows
    # (their count would be 1, trivially) and on probe failure /
    # timeout. Populated post-search via a lightweight per-result
    # `yt-dlp --playlist-items 1` fetch (fans out in parallel).
    video_count:     int | None = None


class SearchResponse(BaseModel):
    """Envelope for /content/search."""
    model_config = ConfigDict(extra = "forbid")

    query:       str
    total:       int
    results:     list[VideoSnippet]
    fetched_for: int     # what we asked yt-dlp for (max_results * multiplier when filtered)
    elapsed_s:   float
