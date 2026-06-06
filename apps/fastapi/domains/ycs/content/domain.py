"""ycs/content — PURE functions (no I/O, no async, no clocks).

Cosmic Python "Functional Core" for the content-search subsystem. The
LLM-stage equivalent of `domains/dd/synth/nodes/sawc/domain.py`.

What lives here:
- `build_string_filter` — operator-aware string match-filter formatter
- `build_match_conditions` — translates request filters → yt-dlp expressions
- `build_search_args` — assembles the full `yt-dlp ...` argv
- `pick_best_thumbnail` — chooses the highest-resolution thumbnail
- `normalize_search_entry` — projects raw yt-dlp dict → VideoSnippet shape
"""
from __future__ import annotations

from .params import BASE_ARGS, FETCH_MULTIPLIER_FILTERED
from .patterns import STRING_FILTER_OP_PREFIXES


def build_string_filter(field: str, value: str) -> str:
    """Translate a user-supplied string filter into a yt-dlp match-filter
    expression. Recognizes the operator prefixes from `patterns.py`; any
    other value defaults to a case-insensitive contains."""
    if value.startswith(STRING_FILTER_OP_PREFIXES):
        return f"{field}{value}"
    # Default: contains (operator `*=`), single-quote the literal so
    # whitespace in `value` doesn't break the shell-style yt-dlp parser.
    return f"{field}*='{value}'"


def build_match_conditions(req) -> list[str]:
    """Walk a `SearchRequest`-shaped object and produce the
    `--match-filter` clauses to AND together. Pure: no subprocess, no
    env, no clock."""
    conds: list[str] = []

    # Duration — explicit min/max overrides the preset.
    if req.duration_min is not None or req.duration_max is not None:
        if req.duration_min is not None:
            conds.append(f"duration>={req.duration_min}")
        if req.duration_max is not None:
            conds.append(f"duration<={req.duration_max}")
    elif req.duration == "Under 4 minutes":
        conds.append("duration<240")
    elif req.duration == "4 - 20 minutes":
        conds.append("duration>=240")
        conds.append("duration<=1200")
    elif req.duration == "Over 20 minutes":
        conds.append("duration>1200")

    # `>=?` = optional comparison (missing field passes) — see yt-dlp docs.
    if req.min_views is not None:
        conds.append(f"view_count>=?{req.min_views}")
    if req.max_views is not None:
        conds.append(f"view_count<=?{req.max_views}")
    if req.min_likes is not None:
        conds.append(f"like_count>=?{req.min_likes}")

    if req.live_status:
        conds.append(f"live_status='{req.live_status}'")
    elif req.is_live is True:
        conds.append("is_live")
    elif req.is_live is False:
        conds.append("!is_live")

    if req.availability:
        conds.append(f"availability='{req.availability}'")

    if req.title_contains:
        conds.append(build_string_filter("title", req.title_contains))
    if req.description_contains:
        conds.append(build_string_filter("description", req.description_contains))
    if req.channel_name:
        conds.append(build_string_filter("channel", req.channel_name))

    # Shorts exclusion — duration heuristic. yt-dlp has no native
    # `is_short` field (per the June 2026 filter research); 60s is the
    # YouTube Shorts cap. The `?` operator lets entries with missing
    # duration pass through. The `~='/shorts/'` URL belt-and-suspenders
    # was tried but yt-dlp's match-filter parser handles the quoted
    # regex inconsistently and started 500-ing — duration alone catches
    # the vast majority of shorts in practice.
    if getattr(req, "exclude_shorts", False):
        conds.append("duration>?60")

    return conds


def effective_fetch_count(max_results: int, conditions: list[str]) -> int:
    """Inflate the yt-dlp fetch count by `FETCH_MULTIPLIER_FILTERED` when
    any post-filter is set, so the final list still hits `max_results`
    after rejections."""
    return (
        max_results * FETCH_MULTIPLIER_FILTERED if conditions else max_results
    )


def build_search_args(
    query: str,
    fetch_count: int,
    sort_by_date: bool,
    conditions: list[str],
    date_after: str | None,
    date_before: str | None,
    age_limit: int | None,
) -> list[str]:
    """Compose the full yt-dlp argv for a flat-playlist search. Returns a
    fresh list (BASE_ARGS is a tuple to keep it immutable at module scope).

    `sort_by_date` is kept on the signature for API stability but no longer
    flips the search prefix — yt-dlp's `ytsearchdate:` started returning
    `Unable to handle request` in this stack (yt-dlp 2026.3.17 + the
    current PoT/extractor combo). Service does a Python-side sort by
    `upload_date` instead; results may have fewer dates than `ytsearchdate`
    would have given but never 502s."""
    search_url = f"ytsearch{fetch_count}:{query}"
    args: list[str] = [
        *BASE_ARGS,
        "--flat-playlist",
        "--dump-single-json",
        # Approximate-date attaches a `upload_date` hint to flat entries,
        # enabling date filters on the lighter `--flat-playlist` path.
        "--extractor-args", "youtube:approximate_date",
    ]
    if conditions:
        args.extend(["--match-filter", " & ".join(conditions)])
    if date_after:
        args.extend(["--dateafter", date_after])
    if date_before:
        args.extend(["--datebefore", date_before])
    if age_limit is not None:
        # Overrides the BASE_ARGS `--age-limit 0` on the same argv (last
        # wins in yt-dlp arg parsing).
        args.extend(["--age-limit", str(age_limit)])
    args.append(search_url)
    return args


def pick_best_thumbnail(thumbnails: list[dict] | None) -> str:
    """Return the URL of the highest-resolution thumbnail in the list,
    or "" if none. Resolution = width × height."""
    if not thumbnails:
        return ""
    best = max(
        thumbnails,
        key = lambda t: (t.get("height") or 0) * (t.get("width") or 0),
        default = None,
    )
    return (best or {}).get("url", "") or ""


import re

_PLAYLIST_ID_RE = re.compile(r"^(PL|UU|LL|RD|OL|FL|TL|EL)[A-Za-z0-9_-]{10,}")
_CHANNEL_ID_RE  = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def detect_entry_kind(entry: dict) -> str:
    """Classify a yt-dlp search entry as 'video' | 'channel' | 'playlist'.

    YouTube search results mostly return videos, but channels and
    playlists do appear (especially for query terms that name a creator
    or playlist). The frontend uses the resulting `kind` to badge each
    row and to gate the bulk-action routing — only video selections
    can be sent to the Videos tab, only channels to Channel, etc.

    Precedence is the same as `parsers.js::detectMode` on the frontend
    (playlist > channel > video) so the two halves agree. yt-dlp's
    flat-playlist projection is sparse — `ie_key` / `_type` may be
    missing or non-canonical — so we also check `url`, `webpage_url`,
    and the id pattern. id-only matches lose to URL/ie_key matches.
    """
    ie_key = (entry.get("ie_key") or "").lower()
    entry_type = (entry.get("_type") or "").lower()
    url = entry.get("url") or entry.get("webpage_url") or ""
    entry_id = entry.get("id") or ""
    url_l = url.lower()
    if (
        "playlist" in ie_key
        or entry_type == "playlist"
        or "playlist?list=" in url_l
        or "/playlist/" in url_l
        or _PLAYLIST_ID_RE.match(entry_id)
    ):
        return "playlist"
    if (
        "tab" in ie_key
        or entry_type == "channel"
        or "youtube.com/@" in url_l
        or "youtube.com/channel/" in url_l
        or "youtube.com/c/" in url_l
        or "youtube.com/user/" in url_l
        or _CHANNEL_ID_RE.match(entry_id)
    ):
        return "channel"
    return "video"


def normalize_search_entry(entry: dict) -> dict:
    """Project a raw `--flat-playlist` entry → the dict shape
    `VideoSnippet` validates. Returns {} when the entry has no id (yt-dlp
    occasionally emits null placeholders)."""
    if not entry or not entry.get("id"):
        return {}
    vid = entry["id"]
    kind = detect_entry_kind(entry)
    # Default URL shape depends on kind — videos get watch?v=, channels
    # get channel/UC..., playlists get playlist?list=. Use whatever yt-dlp
    # gave us if present.
    default_url = f"https://www.youtube.com/watch?v={vid}"
    if kind == "channel":
        default_url = f"https://www.youtube.com/channel/{vid}"
    elif kind == "playlist":
        default_url = f"https://www.youtube.com/playlist?list={vid}"
    return {
        "id":              vid,
        "kind":            kind,
        "title":           entry.get("title"),
        "url":             entry.get("url") or default_url,
        "duration":        entry.get("duration"),
        "duration_string": entry.get("duration_string"),
        "view_count":      entry.get("view_count"),
        "like_count":      entry.get("like_count"),
        "channel":         entry.get("channel"),
        "channel_id":      entry.get("channel_id"),
        "channel_url":     entry.get("channel_url"),
        "thumbnail":       entry.get("thumbnail") or pick_best_thumbnail(entry.get("thumbnails")),
        "description":     entry.get("description"),
        "upload_date":     entry.get("upload_date"),
        "timestamp":         entry.get("timestamp"),
        "release_timestamp": entry.get("release_timestamp"),
        "live_status":     entry.get("live_status"),
        "availability":    entry.get("availability"),
    }
