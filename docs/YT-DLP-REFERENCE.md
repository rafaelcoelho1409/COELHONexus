# yt-dlp Reference Guide

> Reference documentation for yt-dlp usage in COELHONexus YouTube endpoints.
> Using subprocess approach for memory safety in long-running FastAPI server.

---

## Why Subprocess over Python Library

| Factor | Python Library (`yt_dlp`) | Subprocess (`subprocess.run`) |
|--------|---------------------------|-------------------------------|
| **Memory Isolation** | Shares process memory - potential accumulation over time | Each call is isolated - memory freed on exit |
| **Memory Leaks** | Reported issues with large playlists/channels (2.4GB+ RAM) | Naturally isolated - process terminates and frees all memory |
| **Startup Overhead** | One-time import, reuse instance | New process each call (~100-200ms overhead) |
| **Error Handling** | Python exceptions, easier to catch | Parse stderr, exit codes |
| **Control** | Full access to all options | CLI options only |
| **JSON Parsing** | Returns Python dict directly | Must parse JSON from stdout |
| **Concurrency** | Need threading/async wrapper | Easy with `asyncio.create_subprocess_exec` |

---

## Subprocess Approach for Each Endpoint

| Endpoint | Command |
|----------|---------|
| `/search` | `yt-dlp --dump-json --flat-playlist "ytsearch{N}:query"` |
| `/videos` | `yt-dlp --dump-json "URL1" "URL2"` or batch file |
| `/channel` | `yt-dlp --dump-json --flat-playlist "channel_url/videos"` |
| `/playlist` | `yt-dlp --dump-json --flat-playlist "playlist_url"` |
| `/transcriptions` | Keep `youtube_transcript_api` (lightweight, works well) |

---

## Search Syntax

| Prefix | Description | Example |
|--------|-------------|---------|
| `ytsearch{N}:query` | YouTube search, N results (relevance) | `ytsearch10:python tutorial` |
| `ytsearchdate{N}:query` | YouTube search sorted by date | `ytsearchdate5:news today` |
| `ytmsearch:query` | YouTube Music search | `ytmsearch:lofi` |

---

## JSON Output Options

| Option | Description |
|--------|-------------|
| `-j, --dump-json` | Print JSON per video (implies simulation) |
| `-J, --dump-single-json` | Single JSON for entire URL/playlist |
| `-O, --print TEMPLATE` | Print specific fields only |
| `--write-info-json` | Save .info.json file |

---

## Available Metadata Fields (info_dict)

### Core Video
`id`, `title`, `fulltitle`, `description`, `ext`, `duration`, `duration_string`, `thumbnail`, `thumbnails`, `webpage_url`

### Channel/Uploader
`channel`, `channel_id`, `channel_url`, `channel_follower_count`, `uploader`, `uploader_id`, `uploader_url`

### Dates
`upload_date` (YYYYMMDD), `timestamp`, `release_date`, `release_year`, `modified_date`

### Engagement
`view_count`, `like_count`, `dislike_count`, `comment_count`, `average_rating`, `repost_count`

### Classification
`tags`, `categories`, `age_limit`, `availability`, `live_status`, `is_live`, `was_live`

### Playlist
`playlist`, `playlist_id`, `playlist_title`, `playlist_index`, `playlist_count`, `n_entries`, `playlist_uploader`, `playlist_uploader_id`

### Media
`formats` (list), `subtitles` (dict), `automatic_captions` (dict), `chapters`, `heatmap`

---

## Playlist/Channel Options

| Option | Description |
|--------|-------------|
| `--flat-playlist` | Fast extraction, minimal metadata per entry |
| `--no-flat-playlist` | Full extraction for each video (default) |
| `-I, --playlist-items SPEC` | Select items: `"1:3,7,-5::2"` |
| `--playliststart N` | Start at index N (1-indexed) |
| `--playlistend N` | End at index N |
| `--lazy-playlist` | Process entries as received (streaming) |

---

## Subtitle Options

| Option | Description |
|--------|-------------|
| `--write-subs` | Write manual subtitles |
| `--write-auto-subs` | Write auto-generated captions |
| `--list-subs` | List available subtitles |
| `--sub-format FORMAT` | Format: `srt`, `vtt`, `ass`, `json3` |
| `--sub-langs LANGS` | Languages: `"en,pt"` or `"en.*"` or `"all"` |

---

## Python Async Subprocess Example

```python
import asyncio
import orjson

async def yt_dlp_extract(url: str, opts: list[str]) -> dict:
    """Memory-safe yt-dlp extraction using subprocess."""
    cmd = ["yt-dlp", "--dump-json", *opts, url]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise Exception(stderr.decode())

    return orjson.loads(stdout)
```

---

## References

- [yt-dlp GitHub Repository](https://github.com/yt-dlp/yt-dlp)
- [yt-dlp Arch Manual](https://man.archlinux.org/man/yt-dlp.1)
- [yt-dlp Information Extraction Pipeline - DeepWiki](https://deepwiki.com/yt-dlp/yt-dlp/2.2-information-extraction-pipeline)
- [Get YouTube Metadata with Python - Hrekov](https://www.hrekov.com/blog/youtube-metadata-python-yt-dlp)
- [Searching YouTube with yt-dlp](https://write.corbpie.com/searching-youtube-videos-with-yt-dlp/)

---

*Last updated: 2026-03-25*
