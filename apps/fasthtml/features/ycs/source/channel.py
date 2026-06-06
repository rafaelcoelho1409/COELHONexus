"""Source · Channel mode — paste channel @handles/URLs/IDs, dispatch
one Celery extract per channel. POST `/api/v1/ycs/content/channel`.

Sequential batch shape (June 2026 refactor) — mirrors the Videos tab:
  - Textarea for bulk paste, one per line (or CSV/whitespace-separated).
  - Each line parses → chip with status glyph (✓ valid channel /
    × unrecognized) and live count above.
  - Drag-drop .txt/.csv onto the textarea.
  - Submit fires N POSTs in parallel — one Celery task per channel —
    and redirects to the Ingest step on the first returned task id.
  - max_results + transcripts collapsed under `Options ▸`.

The Search tab's bulk action bar pushes selected channels here via
`ycs:route` (only items whose `kind === "channel"` are routed)."""
from __future__ import annotations

from fasthtml.common import Button, Div, Form, P, Textarea

from .widgets import _OptionsCollapse, _TranscriptOptions


def ChannelTab():
    return Div(
        P(
            "Paste channel @handles, URLs, or IDs — one per line, or "
            "comma-separated. Drop a .txt or .csv too. Each channel is "
            "queued as its own background task.",
            cls = "ycs-tab-hint",
        ),
        Form(
            Textarea(
                name        = "channel_ids",
                id          = "ycs-channel-input",
                placeholder = (
                    "@anthropicai\n"
                    "https://www.youtube.com/@openai\n"
                    "UC_x5XG1OV2P6uZZ5FSM9Ttw"
                ),
                rows        = "6",
                cls         = "ycs-input ycs-textarea",
                required    = True,
            ),
            Div(
                Div("", cls = "ycs-paste-count", id = "ycs-channel-count"),
                Div("", cls = "ycs-paste-chips", id = "ycs-channel-chips"),
                cls = "ycs-paste-preview",
                id  = "ycs-channel-preview",
                data_state = "empty",
            ),
            _OptionsCollapse(
                _TranscriptOptions("channel"),
                prefix = "channel",
            ),
            Div(
                Div("", id = "ycs-channel-status", cls = "ycs-search-status"),
                Button(
                    "Start ingest",
                    type = "submit",
                    cls  = "btn-primary",
                    disabled = True,
                    id = "ycs-channel-submit",
                ),
                cls = "ycs-form-actions",
            ),
            id = "ycs-channel-form",
        ),
        cls = "ycs-tab-body",
        id  = "ycs-tab-channel",
        role = "tabpanel",
    )
