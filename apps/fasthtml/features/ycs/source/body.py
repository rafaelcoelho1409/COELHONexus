"""Step 1 · Source — pick videos to ingest.

Four modes, each its own `.ycs-tab-body` panel emitted into the body;
the active mode is toggled by `source.js` reacting to the row-3
`SourceModeTabs` pills (see chrome.py). The 4-mode tab strip lives in
the shell's row 3 (`StageToolbar`, wired in routes.py).

Tab modules:
  search.py    — Search (sync yt-dlp metadata, in-page results)
  videos.py    — Videos (Celery dispatch → /ingest?task=…)
  channel.py   — Channel (Celery dispatch)
  playlist.py  — Playlist (Celery dispatch)

Shared form widgets live in `widgets.py` (filter-field label + the
transcript-options row used by 3/4 tabs)."""
from __future__ import annotations

from fasthtml.common import Div

from .channel import ChannelTab
from .playlist import PlaylistTab
from .search import SearchTab
from .videos import VideosTab


def SourceBody(slug: str | None):
    return Div(
        SearchTab(),
        VideosTab(),
        PlaylistTab(),
        ChannelTab(),
        cls = "ycs-source-panels",
    )
