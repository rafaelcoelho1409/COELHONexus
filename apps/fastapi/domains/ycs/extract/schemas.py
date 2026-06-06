"""ycs/extract — Pydantic boundary schemas (request inputs + per-video metadata).

Mirror of deprecated `schemas/youtube/inputs.py` VideosRequest /
ChannelRequest / PlaylistRequest + the projected video shape from
`helpers.py:L441-531` (`YtDlpExtractor._normalize_video`)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from domains.ycs.content.schemas import NonEmptyStr


class VideosRequest(BaseModel):
    """Paste video IDs / URLs. `include_transcription` toggles whether the
    Celery task also fetches transcripts via Playwright after the yt-dlp
    metadata pass (deprecated `tasks/youtube/crawler.py:L57-95`)."""
    model_config = ConfigDict(extra = "forbid")

    video_ids:               list[NonEmptyStr] = Field(..., min_length = 1, max_length = 500)
    include_transcription:   bool = True
    transcription_languages: list[NonEmptyStr] | None = None


class ChannelRequest(BaseModel):
    """`channel_id` accepts: bare ID (UC…), full URL, or @handle.
    `max_results = 0` means "all videos" (deprecated convention)."""
    model_config = ConfigDict(extra = "forbid")

    channel_id:              NonEmptyStr
    max_results:             int = Field(default = 10, ge = 0, le = 2000)
    include_transcription:   bool = True
    transcription_languages: list[NonEmptyStr] | None = None


class PlaylistRequest(BaseModel):
    """`playlist_id` accepts: bare ID (PL/UU/RD…), full URL with ?list=…"""
    model_config = ConfigDict(extra = "forbid")

    playlist_id:             NonEmptyStr
    max_results:             int = Field(default = 10, ge = 0, le = 2000)
    include_transcription:   bool = True
    transcription_languages: list[NonEmptyStr] | None = None


class VideoMetadata(BaseModel):
    """Per-video record returned by the extractor. Mirror of deprecated
    `helpers.py:L441-531` (`YtDlpExtractor._normalize_video`)."""
    model_config = ConfigDict(extra = "allow")

    id:                  str
    title:               str | None = None
    fulltitle:           str | None = None
    description:         str | None = None
    webpage_url:         str | None = None
    original_url:        str | None = None
    thumbnail_url:       str | None = None
    channel:             str | None = None
    channel_id:          str | None = None
    channel_url:         str | None = None
    channel_follower_count:  int | None = None
    channel_is_verified:     bool | None = None
    uploader:            str | None = None
    uploader_id:         str | None = None
    uploader_url:        str | None = None
    upload_date:         str | None = None
    timestamp:           int | None = None
    duration:            int | None = None
    duration_string:     str | None = None
    view_count:          int | None = None
    like_count:          int | None = None
    dislike_count:       int | None = None
    comment_count:       int | None = None
    average_rating:      float | None = None
    age_limit:           int | None = None
    availability:        str | None = None
    is_live:             bool | None = None
    was_live:            bool | None = None
    live_status:         str | None = None
    categories:          list[str] = Field(default_factory = list)
    tags:                list[str] = Field(default_factory = list)
    chapters:            list[dict] = Field(default_factory = list)
    subtitles:           list[str] = Field(default_factory = list)
    automatic_captions:  list[str] = Field(default_factory = list)
    playlist:            str | None = None
    playlist_id:         str | None = None
    playlist_title:      str | None = None
    playlist_index:      int | None = None
    playlist_count:      int | None = None
    extractor:           str | None = None
    extractor_key:       str | None = None
    extracted_at:        str | None = None


class PlaylistResult(BaseModel):
    """Full playlist envelope (deprecated `helpers.py:L373-383`)."""
    model_config = ConfigDict(extra = "forbid")

    playlist_id:          str | None = None
    playlist_title:       str | None = None
    playlist_url:         str
    playlist_description: str | None = None
    playlist_uploader:    str | None = None
    playlist_uploader_id: str | None = None
    playlist_count:       int | None = None
    total_videos:         int
    videos:               list[VideoMetadata]


class ChannelResult(BaseModel):
    """Channel envelope. Mirror of deprecated `helpers.py:L391-439` (the
    channel branch builds the same shape as playlist)."""
    model_config = ConfigDict(extra = "forbid")

    channel_id:        str | None = None
    channel_title:     str | None = None
    channel_url:       str
    channel_uploader:  str | None = None
    total_videos:      int
    videos:            list[VideoMetadata]
