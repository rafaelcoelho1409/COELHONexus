"""YCS metadata search — yt-dlp ytsearch with rich filter set.

No transcript fetch, no ingestion. Returns normalized video metadata dicts
so the UI can render YouTube-style result cards and the user can pick
which videos to ingest in step 2.

Mirrors the legacy POST /search (zdeprecated/.../routers/v1/youtube/
content.py + helpers.YtDlpExtractor.search) — same filter semantics,
same field names — but built on yt-dlp's Python API so we share the same
PO-token sidecar + ignoreerrors + IPv4 settings as ingestion.py.
"""
import asyncio
import re
from typing import Any, Literal

import yt_dlp
from yt_dlp.utils import DateRange, match_filter_func


DurationPreset = Literal[
    "Under 4 minutes",
    "4 - 20 minutes",
    "Over 20 minutes",
]
LiveStatus = Literal[
    "not_live", "is_live", "is_upcoming", "was_live", "post_live",
]
Availability = Literal[
    "public", "unlisted", "private",
    "premium_only", "subscriber_only", "needs_auth",
]


# Shared yt-dlp options across every Source-stage enumeration mode
# (Search / Videos / Playlist / Channel). Each caller layers on its own
# `extract_flat` value because flat-playlist is right for collection
# listings but wrong for per-video metadata.
_BASE_OPTS: dict[str, Any] = {
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": True,
    "source_address": "0.0.0.0",
    "socket_timeout": 15,
    "retries": 3,
    "extractor_args": {
        "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4416"]},
        "youtube": {
            "approximate_date": ["true"],
            "skip": ["dash", "hls", "translated_subs"],
        },
    },
}


def _string_filter(field: str, value: str) -> str:
    """Map a user value to a yt-dlp match-filter clause.

    Accepted operator prefixes match yt-dlp's own syntax:
      *=  contains    ^=  starts with    $=  ends with    ~=  regex
      !*= !^= !$= !~= negations of the above             =   exact
    No prefix = `field*=value` (case-insensitive contains).
    """
    operators = ("*=", "^=", "$=", "~=", "!*=", "!^=", "!$=", "!~=", "=")
    if value.startswith(operators):
        return f"{field}{value}"
    return f"{field}*={value}"


def _build_match_filter(
    *,
    duration: DurationPreset | None,
    duration_min: int | None,
    duration_max: int | None,
    min_views: int | None,
    max_views: int | None,
    min_likes: int | None,
    is_live: bool | None,
    live_status: LiveStatus | None,
    availability: Availability | None,
    title_contains: str | None,
    description_contains: str | None,
    channel_name: str | None,
) -> str | None:
    conds: list[str] = []
    if duration_min is not None or duration_max is not None:
        if duration_min is not None:
            conds.append(f"duration>={duration_min}")
        if duration_max is not None:
            conds.append(f"duration<={duration_max}")
    elif duration == "Under 4 minutes":
        conds.append("duration<240")
    elif duration == "4 - 20 minutes":
        conds.append("duration>=240")
        conds.append("duration<=1200")
    elif duration == "Over 20 minutes":
        conds.append("duration>1200")
    # The `?` makes the comparison pass when the field is missing — matches
    # the legacy behavior so unscored videos aren't silently dropped.
    if min_views is not None:
        conds.append(f"view_count>=?{min_views}")
    if max_views is not None:
        conds.append(f"view_count<=?{max_views}")
    if min_likes is not None:
        conds.append(f"like_count>=?{min_likes}")
    if live_status:
        conds.append(f"live_status={live_status}")
    elif is_live is True:
        conds.append("is_live")
    elif is_live is False:
        conds.append("!is_live")
    if availability:
        conds.append(f"availability={availability}")
    if title_contains:
        conds.append(_string_filter("title", title_contains))
    if description_contains:
        conds.append(_string_filter("description", description_contains))
    if channel_name:
        conds.append(_string_filter("channel", channel_name))
    if not conds:
        return None
    return " & ".join(conds)


def _normalize(entry: dict[str, Any]) -> dict[str, Any]:
    vid_id = entry.get("id")
    return {
        "id": vid_id,
        "title": entry.get("title"),
        "url": entry.get("url") or f"https://www.youtube.com/watch?v={vid_id}",
        "duration": entry.get("duration"),
        "duration_string": entry.get("duration_string"),
        "view_count": entry.get("view_count"),
        "like_count": entry.get("like_count"),
        "channel": entry.get("channel"),
        "channel_id": entry.get("channel_id"),
        "channel_url": entry.get("channel_url"),
        "thumbnail": entry.get("thumbnail"),
        "description": entry.get("description"),
        "upload_date": entry.get("upload_date"),
        "live_status": entry.get("live_status"),
        "availability": entry.get("availability"),
    }


def _search_sync(
    query: str,
    max_results: int,
    sort_by_date: bool,
    match_filter_str: str | None,
    date_after: str | None,
    date_before: str | None,
    age_limit: int | None,
    has_filters: bool,
) -> list[dict[str, Any]]:
    # Over-fetch 3x when filters might drop hits so we still surface
    # max_results candidates after filtering. Matches legacy behavior.
    fetch_count = max_results * 3 if has_filters else max_results
    prefix = "ytsearchdate" if sort_by_date else "ytsearch"
    search_url = f"{prefix}{fetch_count}:{query}"
    # Flat-playlist mode = no per-video extraction; we want only the
    # metadata YouTube returns from the search page itself.
    opts: dict[str, Any] = {**_BASE_OPTS, "extract_flat": "in_playlist"}
    if match_filter_str:
        opts["match_filter"] = match_filter_func(match_filter_str)
    if date_after or date_before:
        opts["daterange"] = DateRange(date_after, date_before)
    if age_limit is not None:
        opts["age_limit"] = age_limit

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(search_url, download=False)
    if not info:
        return []
    out: list[dict[str, Any]] = []
    for entry in info.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        out.append(_normalize(entry))
        if len(out) >= max_results:
            break
    return out


async def search_videos(
    query: str,
    max_results: int = 10,
    sort_by_date: bool = False,
    duration: DurationPreset | None = None,
    duration_min: int | None = None,
    duration_max: int | None = None,
    date_after: str | None = None,
    date_before: str | None = None,
    min_views: int | None = None,
    max_views: int | None = None,
    min_likes: int | None = None,
    is_live: bool | None = None,
    live_status: LiveStatus | None = None,
    availability: Availability | None = None,
    age_limit: int | None = None,
    title_contains: str | None = None,
    description_contains: str | None = None,
    channel_name: str | None = None,
) -> list[dict[str, Any]]:
    """Search YouTube and return normalized metadata dicts.

    No transcript fetch, no ingestion — feeds a YouTube-search-style
    results grid the user picks from. Mirrors the deprecated /search
    filter set; raises nothing on empty result (returns []).
    """
    match_filter_str = _build_match_filter(
        duration=duration,
        duration_min=duration_min,
        duration_max=duration_max,
        min_views=min_views,
        max_views=max_views,
        min_likes=min_likes,
        is_live=is_live,
        live_status=live_status,
        availability=availability,
        title_contains=title_contains,
        description_contains=description_contains,
        channel_name=channel_name,
    )
    has_filters = bool(
        match_filter_str
        or date_after
        or date_before
        or age_limit is not None,
    )
    return await asyncio.to_thread(
        _search_sync,
        query, max_results, sort_by_date,
        match_filter_str, date_after, date_before, age_limit,
        has_filters,
    )


# =============================================================================
# Direct-mode URL/ID resolution
# =============================================================================
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL_VIDEO_ID_PATTERNS = [
    re.compile(r"(?:youtube\.com/watch\?(?:.*&)?v=)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtu\.be/)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com/shorts/)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com/live/)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})"),
]


def _resolve_video_id(s: str) -> str | None:
    """Parse a YouTube URL (watch / youtu.be / shorts / live / embed) or
    accept a bare 11-char video ID. Returns the ID or ``None``."""
    s = s.strip()
    if not s:
        return None
    if _VIDEO_ID_RE.fullmatch(s):
        return s
    for pattern in _URL_VIDEO_ID_PATTERNS:
        m = pattern.search(s)
        if m:
            return m.group(1)
    return None


def _resolve_playlist_url(s: str) -> str:
    """Accept a full playlist URL or a bare playlist ID (PL... / OL... etc)."""
    s = s.strip()
    if s.startswith(("http://", "https://")):
        return s
    return f"https://www.youtube.com/playlist?list={s}"


_CHANNEL_TAB_SUFFIXES = ("/videos", "/shorts", "/streams", "/playlists", "/community")


def _resolve_channel_videos_url(s: str) -> str:
    """Accept a full channel URL, a bare ``UCxxx`` ID, or an ``@handle``.
    Always lands on the ``/videos`` tab so flat-playlist iterates regular
    uploads (skips Shorts/Live by default — per yt-dlp issue #11976)."""
    s = s.strip().rstrip("/")
    if s.startswith(("http://", "https://")):
        if not any(s.endswith(suffix) for suffix in _CHANNEL_TAB_SUFFIXES):
            s = f"{s}/videos"
        return s
    if s.startswith("UC"):
        return f"https://www.youtube.com/channel/{s}/videos"
    if s.startswith("@"):
        return f"https://www.youtube.com/{s}/videos"
    # Bare handle without @ — assume it's a handle and prepend one.
    return f"https://www.youtube.com/@{s}/videos"


# =============================================================================
# Direct-mode enumeration: Videos / Playlist / Channel
# =============================================================================
def _enumerate_one_video_sync(video_url: str) -> dict[str, Any] | None:
    """Per-video metadata fetch (not flat-playlist — we want full fields)."""
    opts: dict[str, Any] = {**_BASE_OPTS, "extract_flat": False}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
    if not info or not info.get("id"):
        return None
    return _normalize(info)


async def enumerate_videos(video_inputs: list[str]) -> list[dict[str, Any]]:
    """Fetch full metadata for an explicit list of video IDs or URLs.

    Parses each input (URL or bare 11-char ID), dedupes, fans out parallel
    per-video extractions. Returns the same normalized dict shape as
    :func:`search_videos`. Unresolvable inputs are silently skipped.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for raw in video_inputs:
        vid_id = _resolve_video_id(raw)
        if vid_id and vid_id not in seen:
            seen.add(vid_id)
            urls.append(f"https://www.youtube.com/watch?v={vid_id}")
    if not urls:
        return []
    tasks = [asyncio.to_thread(_enumerate_one_video_sync, u) for u in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, dict) and r]


def _enumerate_collection_sync(
    url: str, max_results: int,
) -> list[dict[str, Any]]:
    """Flat-playlist enumeration for a playlist URL or channel /videos URL."""
    opts: dict[str, Any] = {**_BASE_OPTS, "extract_flat": "in_playlist"}
    if max_results > 0:
        opts["playlistend"] = max_results
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return []
    out: list[dict[str, Any]] = []
    for entry in info.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        out.append(_normalize(entry))
    return out


async def enumerate_playlist(
    playlist: str, max_results: int = 0,
) -> list[dict[str, Any]]:
    """Enumerate a YouTube playlist via flat-playlist. ``max_results=0``
    fetches the whole playlist; otherwise yt-dlp's ``playlistend`` caps it."""
    return await asyncio.to_thread(
        _enumerate_collection_sync,
        _resolve_playlist_url(playlist),
        max_results,
    )


async def enumerate_channel(
    channel: str, max_results: int = 0,
) -> list[dict[str, Any]]:
    """Enumerate a YouTube channel's /videos tab via flat-playlist.
    ``channel`` may be a full URL, an ``@handle``, or a ``UCxxx`` ID."""
    return await asyncio.to_thread(
        _enumerate_collection_sync,
        _resolve_channel_videos_url(channel),
        max_results,
    )
