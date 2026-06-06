"""ycs/extract — PURE helpers (input normalization + argv builders + projection).

Functional Core (per `docs/CODE-CONVENTIONS.md` §4) — no I/O, no
subprocess, no clock other than `_utc_now_iso` (which is a single
isolated call used by the projection — deprecated did the same).

Mirror of deprecated `YtDlpExtractor` static logic + URL/ID parsing
that lived inline in `helpers.py`."""
from __future__ import annotations

from datetime import datetime, timezone

from domains.ycs.content.domain import pick_best_thumbnail
from domains.ycs.content.params import BASE_ARGS

from .errors import (
    InvalidChannelIdError,
    InvalidPlaylistIdError,
    InvalidVideoIdError,
)
from .params import (
    MAX_AGGREGATE_TIMEOUT_S,
    MIN_AGGREGATE_TIMEOUT_S,
    SECONDS_PER_VIDEO,
)
from .patterns import (
    CHANNEL_HANDLE_RE,
    CHANNEL_UC_RE,
    CHANNEL_URL_HANDLE_RE,
    PLAYLIST_ID_RE,
    VIDEO_ID_RE,
    WATCH_V_RE,
)


# ---------- Input normalization -----------------------------------------

def normalize_video_id(raw: str) -> str:
    """Accept a bare id, a watch URL, a youtu.be URL, a shorts URL."""
    if not raw:
        raise InvalidVideoIdError("empty")
    s = raw.strip()
    if VIDEO_ID_RE.fullmatch(s):
        return s
    m = WATCH_V_RE.search(s)
    if m:
        return m.group(1)
    raise InvalidVideoIdError(f"not a video id or URL: {raw!r}")


def normalize_channel_id(raw: str) -> str:
    """Return either a `UC…` id or a `@handle` (both valid yt-dlp inputs)."""
    if not raw:
        raise InvalidChannelIdError("empty")
    s = raw.strip()
    m = CHANNEL_UC_RE.search(s)
    if m:
        return m.group(1)
    m = CHANNEL_URL_HANDLE_RE.search(s)
    if m:
        return m.group(1)
    if CHANNEL_HANDLE_RE.fullmatch(s):
        return s
    raise InvalidChannelIdError(f"not a channel id or @handle: {raw!r}")


def normalize_playlist_id(raw: str) -> str:
    if not raw:
        raise InvalidPlaylistIdError("empty")
    s = raw.strip()
    m = PLAYLIST_ID_RE.search(s)
    if m:
        return m.group(1)
    raise InvalidPlaylistIdError(f"not a playlist id or URL: {raw!r}")


def normalize_video_ids(raws: list[str]) -> tuple[list[str], list[str]]:
    """Returns (valid_ids, rejected_inputs). Order preserved; duplicates
    dropped."""
    valid: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for raw in raws or []:
        try:
            vid = normalize_video_id(raw)
        except InvalidVideoIdError:
            rejected.append(raw)
            continue
        if vid in seen:
            continue
        seen.add(vid)
        valid.append(vid)
    return valid, rejected


# ---------- argv builders -----------------------------------------------

def build_video_args(video_id: str) -> list[str]:
    """Full single-video metadata extraction (`--dump-json`).
    Mirror of deprecated `helpers.py:L128-133`."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    return [*BASE_ARGS, "--dump-json", "--no-playlist", url]


def build_playlist_args(playlist_id: str, max_videos: int) -> list[str]:
    """Mirror of deprecated `helpers.py:L352-362`."""
    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    args: list[str] = [
        *BASE_ARGS,
        "--dump-single-json",
        "--match-filter", "availability != subscriber_only",
        "--match-filter", "availability != premium_only",
        "--match-filter", "availability != needs_auth",
    ]
    if max_videos > 0:
        args.extend(["--playlist-end", str(max_videos)])
    args.append(url)
    return args


def build_channel_args(channel_id_or_handle: str, max_videos: int) -> list[str]:
    """Mirror of deprecated `helpers.py:L400-419`."""
    if channel_id_or_handle.startswith("UC"):
        url = f"https://www.youtube.com/channel/{channel_id_or_handle}/videos"
    else:
        url = f"https://www.youtube.com/{channel_id_or_handle}/videos"
    args: list[str] = [
        *BASE_ARGS,
        "--dump-single-json",
        "--match-filter", "availability != subscriber_only",
        "--match-filter", "availability != premium_only",
        "--match-filter", "availability != needs_auth",
    ]
    if max_videos > 0:
        args.extend(["--playlist-end", str(max_videos)])
    args.append(url)
    return args


def aggregate_timeout_s(max_videos: int) -> float:
    """Dynamic wall-clock budget for playlist / channel extraction
    (deprecated `helpers.py:L349,L398`): 10s per video clamped to
    [120s, 1800s]."""
    if max_videos <= 0:
        return float(MAX_AGGREGATE_TIMEOUT_S)
    raw = max_videos * SECONDS_PER_VIDEO
    return float(max(MIN_AGGREGATE_TIMEOUT_S, min(MAX_AGGREGATE_TIMEOUT_S, raw)))


# ---------- Normalization (yt-dlp dict → VideoMetadata-shaped dict) -----

def _utc_now_iso() -> str:
    """Single isolated clock read for the `extracted_at` field."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_full_video(data: dict) -> dict:
    """Project a `--dump-json` dict → the dict shape `VideoMetadata`
    validates. Mirror of deprecated `helpers.py:L441-531`."""
    if not data:
        return {}
    chapters = [
        {
            "title":      (ch or {}).get("title", ""),
            "start_time": (ch or {}).get("start_time", 0),
            "end_time":   (ch or {}).get("end_time", 0),
        }
        for ch in (data.get("chapters") or [])
    ]
    return {
        "id":                     data.get("id", ""),
        "title":                  data.get("title", ""),
        "fulltitle":              data.get("fulltitle", ""),
        "description":            data.get("description", ""),
        "webpage_url":            data.get("webpage_url", ""),
        "original_url":           data.get("original_url", ""),
        "thumbnail_url":          pick_best_thumbnail(data.get("thumbnails") or []),
        "channel":                data.get("channel", ""),
        "channel_id":             data.get("channel_id", ""),
        "channel_url":            data.get("channel_url", ""),
        "channel_follower_count": data.get("channel_follower_count"),
        "channel_is_verified":    data.get("channel_is_verified", False),
        "uploader":               data.get("uploader", ""),
        "uploader_id":            data.get("uploader_id", ""),
        "uploader_url":           data.get("uploader_url", ""),
        "upload_date":            data.get("upload_date", ""),
        "timestamp":              data.get("timestamp"),
        "duration":               data.get("duration"),
        "duration_string":        data.get("duration_string", ""),
        "view_count":             data.get("view_count"),
        "like_count":             data.get("like_count"),
        "dislike_count":          data.get("dislike_count"),
        "comment_count":          data.get("comment_count"),
        "average_rating":         data.get("average_rating"),
        "age_limit":              data.get("age_limit", 0),
        "availability":           data.get("availability", ""),
        "is_live":                data.get("is_live", False),
        "was_live":               data.get("was_live", False),
        "live_status":            data.get("live_status", ""),
        "categories":             data.get("categories") or [],
        "tags":                   data.get("tags") or [],
        "chapters":               chapters,
        "subtitles":              list((data.get("subtitles") or {}).keys()),
        "automatic_captions":     list((data.get("automatic_captions") or {}).keys()),
        "playlist":               data.get("playlist"),
        "playlist_id":            data.get("playlist_id"),
        "playlist_title":         data.get("playlist_title"),
        "playlist_index":         data.get("playlist_index"),
        "playlist_count":         data.get("playlist_count"),
        "extractor":              data.get("extractor", ""),
        "extractor_key":          data.get("extractor_key", ""),
        "extracted_at":           _utc_now_iso(),
    }
