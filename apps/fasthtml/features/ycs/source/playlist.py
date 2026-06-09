"""Source · Playlist mode — paste ONE playlist, browse + pick a subset
of its videos, dispatch the SELECTED video_ids to the videos pipeline.

Same shape as the Channel tab — see channel.py for the design rationale
(SOTA from PatternFly / Helios / Carbon + NN/g pagination guidance)."""
from __future__ import annotations

from fasthtml.common import Button, Div, Form, Input

from .widgets import _InfoPopover, _StickyOptionsBar


def PlaylistTab():
    return Div(
        Form(
            # Sticky URL row — same pattern as Search tab.
            Div(
                Div(
                    Input(
                        type        = "text",
                        name        = "playlist_id",
                        id          = "ycs-playlist-input",
                        placeholder = "https://www.youtube.com/playlist?list=PLrAXtmRdnEQy… · PLrAXtmRdnEQy…",
                        cls         = "ycs-input",
                        required    = True,
                        autocomplete = "off",
                    ),
                    Div(
                        _InfoPopover(
                            "Paste ONE playlist — full URL or `PL…` / `UU…` / "
                            "`OL…` ID. Click Fetch videos to load it, then "
                            "select which videos to ingest.",
                        ),
                        Button(
                            "Fetch videos",
                            type = "submit",
                            cls  = "btn-primary",
                            id   = "ycs-playlist-fetch",
                        ),
                        cls = "ycs-source-row-actions",
                    ),
                    cls = "ycs-source-row",
                ),
                cls = "ycs-source-sticky",
            ),
            Div(
                id  = "ycs-playlist-picker",
                cls = "ycs-picker",
                data_state = "empty",
            ),
            _StickyOptionsBar(
                "playlist",
                Button(
                    "Start Ingestion",
                    type = "submit",
                    cls  = "btn-primary",
                    disabled = True,
                    id = "ycs-playlist-submit",
                    formnovalidate = True,
                ),
                status_id = "ycs-playlist-status",
                extra_actions = (
                    Button(
                        "Ingest all",
                        type = "button",
                        cls  = "ycs-sticky-bar-ingest-all",
                        disabled = True,
                        id = "ycs-playlist-ingest-all",
                        title = (
                            "Queue every video in the playlist, bypassing the "
                            "100-per-page picker cap."
                        ),
                    ),
                ),
            ),
            id = "ycs-playlist-form",
        ),
        cls = "ycs-tab-body",
        id  = "ycs-tab-playlist",
        role = "tabpanel",
    )
