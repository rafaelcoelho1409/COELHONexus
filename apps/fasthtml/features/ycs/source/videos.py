"""Source · Videos mode — paste video IDs/URLs, click Fetch videos,
pick a subset, dispatch the SELECTED video_ids to the ingest pipeline.

Same shape as Channel/Playlist (June 2026 redesign):
  - Textarea + (ⓘ) info popover + Fetch videos button in one row
  - Picker container fills with thumbnails + titles after Fetch
  - Sticky bottom bar carries transcript options + Start Ingestion

Instructions previously lived in a `<P>` at the top of every tab;
they moved into the info-popover icon so the URL/Fetch row breathes
(2026-06-08 polish)."""
from __future__ import annotations

from fasthtml.common import Button, Div, Form, Textarea

from .widgets import _InfoPopover, _StickyOptionsBar


def VideosTab():
    return Div(
        Form(
            # Sticky URL row — same posture as the Search tab's
            # `.ycs-search-sticky` wrapper. Pins the input + Fetch
            # button to the top while the picker scrolls so the user
            # can re-paste / re-fetch without scrolling back up.
            Div(
                Div(
                    Textarea(
                        name        = "video_ids",
                        id          = "ycs-videos-input",
                        placeholder = (
                            "dQw4w9WgXcQ\n"
                            "https://www.youtube.com/watch?v=…\n"
                            "VIDEO_ID, VIDEO_ID, …"
                        ),
                        rows        = "4",
                        cls         = "ycs-input ycs-textarea",
                        required    = True,
                    ),
                    Div(
                        _InfoPopover(
                            "Paste YouTube video IDs or URLs — one per line, "
                            "or comma-separated. Drop a .txt or .csv file too. "
                            "Click Fetch videos to preview metadata, then "
                            "select which ones to ingest.",
                        ),
                        Button(
                            "Fetch videos",
                            type = "submit",
                            cls  = "btn-primary",
                            id   = "ycs-videos-fetch",
                        ),
                        cls = "ycs-source-row-actions",
                    ),
                    cls = "ycs-source-row",
                ),
                cls = "ycs-source-sticky",
            ),
            # The picker container — wirePickerTab fills it once the
            # preview endpoint returns. Master+row checkbox + filter +
            # Load-more all handled by the shared picker.js.
            Div(
                id  = "ycs-videos-picker",
                cls = "ycs-picker",
                data_state = "empty",
            ),
            _StickyOptionsBar(
                "videos",
                Button(
                    "Start Ingestion",
                    type = "submit",
                    cls  = "btn-primary",
                    disabled = True,
                    id = "ycs-videos-submit",
                    formnovalidate = True,
                ),
                status_id = "ycs-videos-status",
            ),
            id = "ycs-videos-form",
        ),
        cls = "ycs-tab-body",
        id  = "ycs-tab-videos",
        role = "tabpanel",
    )
