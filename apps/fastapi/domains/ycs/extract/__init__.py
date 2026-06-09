"""ycs/extract — yt-dlp metadata extraction (deprecated `YtDlpExtractor`).

Sibling of `domains/ycs/content/` which exposes the sync `search` path
on the same `YtDlpExtractor`. This module surfaces the other four
extraction methods (`extract_video`, `extract_batch`, `extract_playlist`,
`extract_channel`).

Per `docs/CODE-CONVENTIONS.md` §4: `domain.py` is pure (argv builders +
URL/ID normalization + result projection); `service.py` is the async
shell around `asyncio.create_subprocess_exec(...)`. Persistence is NOT
done here — Wave 4 Celery tasks wrap these calls + write Elasticsearch.

Public:
  YtDlpExtractor                — service class
  get_extractor()               — singleton accessor
  VideoMetadata / PlaylistResult / ChannelResult  — response shapes
  VideosRequest / ChannelRequest / PlaylistRequest — request shapes
  Invalid*IdError                — exceptions for caller translation"""
from .domain import (
    aggregate_timeout_s,
    build_channel_args,
    build_playlist_args,
    build_video_args,
    normalize_channel_id,
    normalize_full_video,
    normalize_playlist_id,
    normalize_video_id,
    normalize_video_ids,
)
from .errors import (
    ExtractError,
    InvalidChannelIdError,
    InvalidPlaylistIdError,
    InvalidVideoIdError,
)
from .schemas import (
    ChannelPipelineRequest,
    ChannelRequest,
    ChannelResult,
    PlaylistPipelineRequest,
    PlaylistRequest,
    PlaylistResult,
    VideoMetadata,
    VideosRequest,
)
from .service import YtDlpExtractor, get_extractor


__all__ = [
    "ChannelPipelineRequest",
    "ChannelRequest",
    "ChannelResult",
    "ExtractError",
    "InvalidChannelIdError",
    "InvalidPlaylistIdError",
    "InvalidVideoIdError",
    "PlaylistPipelineRequest",
    "PlaylistRequest",
    "PlaylistResult",
    "VideoMetadata",
    "VideosRequest",
    "YtDlpExtractor",
    "aggregate_timeout_s",
    "build_channel_args",
    "build_playlist_args",
    "build_video_args",
    "get_extractor",
    "normalize_channel_id",
    "normalize_full_video",
    "normalize_playlist_id",
    "normalize_video_id",
    "normalize_video_ids",
]
