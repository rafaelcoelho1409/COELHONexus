"""ycs/extract — pre-compiled regex for URL / id normalization.

Deprecated parsed these inline; we lift them per `docs/CODE-CONVENTIONS.md`
§2 (`patterns.py` = pre-compiled regex at module scope)."""
from __future__ import annotations

import re


# Standard YouTube 11-char id (URL-safe base64 alphabet).
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Watch URL — captures the id from `?v=...` or `youtu.be/{id}` or
# `youtube.com/shorts/{id}` or `embed/{id}`.
WATCH_V_RE = re.compile(
    r"(?:youtube\.com/watch\?[^#]*[?&]v=|youtu\.be/|youtube\.com/shorts/|"
    r"youtube\.com/embed/|youtube\.com/v/)([A-Za-z0-9_-]{11})",
)

# Channel — `@handle`, `UC…` id, `youtube.com/@handle`.
CHANNEL_HANDLE_RE = re.compile(r"@[A-Za-z0-9._-]{2,}")
CHANNEL_UC_RE = re.compile(r"(UC[A-Za-z0-9_-]{22})")
CHANNEL_URL_HANDLE_RE = re.compile(r"youtube\.com/(@[A-Za-z0-9._-]{2,})")

# Playlist — `PL…`, `UU…`, `RD…`, `LL…`, `FL…` 32+ chars.
PLAYLIST_ID_RE = re.compile(r"((?:PL|UU|RD|LL|FL)[A-Za-z0-9_-]{10,40})")
