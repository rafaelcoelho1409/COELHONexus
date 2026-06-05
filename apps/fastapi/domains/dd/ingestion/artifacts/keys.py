from __future__ import annotations


# MIME ↔ ext. Served content-type comes from the put_object record, not ext.
MIME_EXT: dict[str, str] = {
    "image/png":                 "png",
    "image/jpeg":                "jpg",
    "image/jpg":                 "jpg",
    "image/gif":                 "gif",
    "image/svg+xml":             "svg",
    "image/webp":                "webp",
    "image/avif":                "avif",
    "image/x-icon":              "ico",
    "image/vnd.microsoft.icon":  "ico",
    "image/bmp":                 "bmp",
    "image/tiff":                "tiff",
    "video/mp4":                 "mp4",
    "video/webm":                "webm",
    "video/quicktime":           "mov",
    "video/x-matroska":          "mkv",
    "video/ogg":                 "ogv",
    "audio/mpeg":                "mp3",
    "audio/ogg":                 "ogg",
    "audio/wav":                 "wav",
    "audio/x-wav":               "wav",
    "audio/mp4":                 "m4a",
    "audio/aac":                 "aac",
    "audio/flac":                "flac",
    "audio/webm":                "weba",
}

EXT_MIME: dict[str, str] = {
    "png":  "image/png",   "jpg":  "image/jpeg",  "jpeg": "image/jpeg",
    "gif":  "image/gif",   "svg":  "image/svg+xml", "webp": "image/webp",
    "avif": "image/avif",  "ico":  "image/x-icon", "bmp":  "image/bmp",
    "tiff": "image/tiff",
    "mp4":  "video/mp4",   "webm": "video/webm",  "mov":  "video/quicktime",
    "mkv":  "video/x-matroska", "ogv": "video/ogg",
    "mp3":  "audio/mpeg",  "ogg":  "audio/ogg",   "wav":  "audio/wav",
    "m4a":  "audio/mp4",   "aac":  "audio/aac",   "flac": "audio/flac",
    "weba": "audio/webm",
}


# (tag, attr) to extract URLs from. `data-src` = lazy-load; srcset handled
# separately (multi-candidate).
ARTIFACT_ATTRS: tuple[tuple[str, str], ...] = (
    ("img",    "src"),
    ("img",    "data-src"),
    ("video",  "src"),
    ("video",  "poster"),
    ("audio",  "src"),
    ("source", "src"),
)


# Eligible for the Sphinx `_images/` fallback probe.
IMAGE_EXTS: frozenset[str] = frozenset({
    "png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "bmp", "tiff", "ico",
})


def public_artifact_path(slug: str, name: str) -> str:
    return f"/api/v1/docs-distiller/ingestion/{slug}/artifacts/{name}"
