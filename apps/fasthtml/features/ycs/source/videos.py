"""Source · Videos mode — paste IDs/URLs, dispatch Celery extract.

POST `/api/v1/ycs/content/videos`. The Celery task redirects to
`/youtube-content-search/ingest?task=<id>` after dispatch.

UI shape (June 2026 SOTA, Linear paste-many-issues idiom):
  - Textarea is the input affordance.
  - On input/blur, videos.js parses each line/CSV token into a chip
    below with a status glyph (✓ valid / ⚠ recovered from URL /
    × unrecognized). Drag-drop .txt/.csv onto the textarea.
  - Live count header above chips: `24 valid · 1 invalid · 0 duplicate`.
  - Submit gated on `valid ≥ 1`."""
from __future__ import annotations

from fasthtml.common import Button, Div, Form, P, Textarea

from .widgets import _OptionsCollapse, _TranscriptOptions


def VideosTab():
    return Div(
        P(
            "Paste YouTube video IDs or URLs — one per line, or comma-"
            "separated. Drop a .txt or .csv file too. We'll parse and "
            "validate each before dispatching the background task.",
            cls = "ycs-tab-hint",
        ),
        Form(
            Textarea(
                name        = "video_ids",
                id          = "ycs-videos-input",
                placeholder = (
                    "dQw4w9WgXcQ\n"
                    "https://www.youtube.com/watch?v=…\n"
                    "VIDEO_ID, VIDEO_ID, …"
                ),
                rows        = "6",
                cls         = "ycs-input ycs-textarea",
                required    = True,
            ),
            Div(
                Div("", cls = "ycs-paste-count", id = "ycs-videos-count"),
                Div("", cls = "ycs-paste-chips", id = "ycs-videos-chips"),
                cls = "ycs-paste-preview",
                id  = "ycs-videos-preview",
                data_state = "empty",
            ),
            _OptionsCollapse(
                _TranscriptOptions(
                    "videos",
                    Button(
                        "Start ingest",
                        type = "submit",
                        cls  = "btn-primary",
                        disabled = True,
                        id = "ycs-videos-submit",
                    ),
                ),
                Div("", id = "ycs-videos-status", cls = "ycs-search-status"),
                prefix = "videos",
            ),
            id = "ycs-videos-form",
        ),
        cls = "ycs-tab-body",
        id  = "ycs-tab-videos",
        role = "tabpanel",
    )
