"""YouTube transcript extraction via yt-dlp + bgutil PO-token plugin.

Returns plain text — VTT scaffolding (timestamps, headers, inline tags)
stripped, overlapping auto-sub cues de-duplicated. Picks the first
available language from `langs`, defaulting to en/pt variants.

Runtime deps (must be installed in the FastAPI image):
  - yt-dlp >= 2026.03.17
  - bgutil-ytdlp-pot-provider >= 1.3.1 (auto-discovered yt-dlp plugin)
  - Deno (external JS runtime required by yt-dlp for signature decipher
    and the PO-token Botguard challenge)
"""
import asyncio
import re
import tempfile
from pathlib import Path

import yt_dlp


_VTT_TIMESTAMP = re.compile(
    r"^\d{2}:\d{2}:\d{2}\.\d{3}\s-->\s\d{2}:\d{2}:\d{2}\.\d{3}.*$",
    re.MULTILINE,
)
_VTT_HEADER = re.compile(r"^WEBVTT.*$|^Kind:.*$|^Language:.*$", re.MULTILINE)
_VTT_TAGS = re.compile(r"<[^>]+>")


def _vtt_to_text(vtt: str) -> str:
    text = _VTT_TAGS.sub("", _VTT_HEADER.sub("", _VTT_TIMESTAMP.sub("", vtt)))
    # YouTube auto-subs emit overlapping cues — each spoken line appears
    # twice as the rolling-caption window slides. Collapse consecutive
    # duplicates so the chunker sees the natural sentence stream.
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line and (not out or out[-1] != line):
            out.append(line)
    return "\n".join(out)


def _fetch_sync(video_url: str, langs: list[str]) -> dict:
    with tempfile.TemporaryDirectory() as td:
        opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": langs,
            "subtitlesformat": "vtt",
            "outtmpl": str(Path(td) / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            # Match the legacy YCS yt-dlp flag set (see zdeprecated/.../
            # routers/v1/youtube/helpers.py BASE_ARGS). Each flag has a
            # real reason: ignoreerrors swallows per-lang 429s so the loop
            # continues to the next available lang; force-ipv4 dodges
            # IPv6-unreachable paths to YouTube edges; socket_timeout +
            # retries bound network hangs; extractor_args wires the PO
            # Token sidecar (port 4416) + skips DASH/HLS manifests + skips
            # auto-translated caption tracks (those generate the 429s).
            "ignoreerrors": True,
            "source_address": "0.0.0.0",
            "socket_timeout": 15,
            "retries": 3,
            "age_limit": 0,
            "extractor_args": {
                "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4416"]},
                "youtube": {"skip": ["dash", "hls", "translated_subs"]},
            },
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=True)

        video_id = info["id"]
        chosen_lang: str | None = None
        vtt_path: Path | None = None
        for lang in langs:
            candidate = Path(td) / f"{video_id}.{lang}.vtt"
            if candidate.exists():
                chosen_lang, vtt_path = lang, candidate
                break
        if vtt_path is None:
            for p in Path(td).glob(f"{video_id}.*.vtt"):
                chosen_lang = p.stem.rsplit(".", 1)[-1]
                vtt_path = p
                break

        return {
            "video_id": video_id,
            "title": info.get("title", ""),
            "transcript_text": _vtt_to_text(vtt_path.read_text("utf-8"))
            if vtt_path
            else "",
            "lang": chosen_lang,
        }


async def fetch_transcript(
    video_url: str,
    langs: list[str] | None = None,
) -> dict:
    """Fetch the transcript for one YouTube video URL.

    Returns ``{video_id, title, transcript_text, lang}``. ``transcript_text``
    is ``""`` and ``lang`` is ``None`` when no subtitles are available
    (caption-disabled videos or PO-token-gated requests without the plugin).
    """
    return await asyncio.to_thread(
        _fetch_sync,
        video_url,
        langs or ["en", "en-US", "en-GB", "pt", "pt-BR"],
    )
