"""Source · Playlist mode — paste playlist URLs/IDs, dispatch one
Celery extract per playlist. POST `/api/v1/ycs/content/playlist`.

Same sequential batch shape as the Channel tab — textarea + paste-
to-chips + N parallel POSTs. The Search tab routes only `kind ===
"playlist"` items here via `ycs:route`."""
from __future__ import annotations

from fasthtml.common import Button, Div, Form, P, Textarea

from .widgets import _OptionsCollapse, _TranscriptOptions


def PlaylistTab():
    return Div(
        P(
            "Paste playlist URLs or IDs — one per line, or comma-"
            "separated. Drop a .txt or .csv too. Each playlist is "
            "queued as its own background task.",
            cls = "ycs-tab-hint",
        ),
        Form(
            Textarea(
                name        = "playlist_ids",
                id          = "ycs-playlist-input",
                placeholder = (
                    "https://www.youtube.com/playlist?list=PLrAXtmRdnEQy…\n"
                    "PLrAXtmRdnEQyzZ-bX5C5zV0p2lKb-V0wD\n"
                    "UU_x5XG1OV2P6uZZ5FSM9Ttw"
                ),
                rows        = "6",
                cls         = "ycs-input ycs-textarea",
                required    = True,
            ),
            Div(
                Div("", cls = "ycs-paste-count", id = "ycs-playlist-count"),
                Div("", cls = "ycs-paste-chips", id = "ycs-playlist-chips"),
                cls = "ycs-paste-preview",
                id  = "ycs-playlist-preview",
                data_state = "empty",
            ),
            _OptionsCollapse(
                _TranscriptOptions("playlist"),
                prefix = "playlist",
            ),
            Div(
                Div("", id = "ycs-playlist-status", cls = "ycs-search-status"),
                Button(
                    "Start ingest",
                    type = "submit",
                    cls  = "btn-primary",
                    disabled = True,
                    id = "ycs-playlist-submit",
                ),
                cls = "ycs-form-actions",
            ),
            id = "ycs-playlist-form",
        ),
        cls = "ycs-tab-body",
        id  = "ycs-tab-playlist",
        role = "tabpanel",
    )
