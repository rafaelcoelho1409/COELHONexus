"""ingestion router — tunables + lookup tables."""
from __future__ import annotations


ARTIFACT_MIME: dict[str, str] = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp",
    "avif": "image/avif", "ico": "image/x-icon", "bmp": "image/bmp",
    "tiff": "image/tiff",
    "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
    "mkv": "video/x-matroska", "ogv": "video/ogg",
    "mp3": "audio/mpeg", "ogg": "audio/ogg", "wav": "audio/wav",
    "m4a": "audio/mp4", "aac": "audio/aac", "flac": "audio/flac",
    "weba": "audio/webm",
}
