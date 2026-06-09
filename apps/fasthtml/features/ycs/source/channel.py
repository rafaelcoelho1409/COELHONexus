"""Source · Channel mode — paste ONE channel, browse + pick a subset of
its videos, dispatch the SELECTED video_ids to `/content/videos/pipeline`.

Replaces the paste-many textarea (June 2026 redesign): the smallest unit
of work is a video, and queuing thousands of videos across multiple
channels at once was easy to do by accident. Now one channel at a time,
with an explicit table picker (master checkbox + per-row + title filter
+ Load more) so the user always sees exactly what they're queuing.

SOTA shape per the parallel WebSearches: PatternFly / Helios / Carbon
converge on master+row checkbox + indeterminate state for partial
selection; NN/g + UXdivers recommend pagination over infinite scroll
for selection (which is a TASK, not browsing); Wipelist confirms the
single-source view + total count + search idiom for YouTube playlist
management at scale."""
from __future__ import annotations

from fasthtml.common import Button, Div, Form, Input

from .widgets import _InfoPopover, _StickyOptionsBar


def ChannelTab():
    return Div(
        Form(
            # Sticky URL row — pins input + Fetch button to the top
            # while the picker scrolls. Mirrors the Search tab's
            # `.ycs-search-sticky` pattern.
            Div(
                Div(
                    Input(
                        type        = "text",
                        name        = "channel_id",
                        id          = "ycs-channel-input",
                        placeholder = "@anthropicai · https://www.youtube.com/@openai · UC_x5XG1OV2P6uZZ5FSM9Ttw",
                        cls         = "ycs-input",
                        required    = True,
                        autocomplete = "off",
                    ),
                    Div(
                        _InfoPopover(
                            "Paste ONE channel — `@handle`, channel URL, or "
                            "`UC…` ID. Click Fetch videos to load its uploads "
                            "playlist, then select which videos to ingest.",
                        ),
                        Button(
                            "Fetch videos",
                            type = "submit",
                            cls  = "btn-primary",
                            id   = "ycs-channel-fetch",
                        ),
                        cls = "ycs-source-row-actions",
                    ),
                    cls = "ycs-source-row",
                ),
                cls = "ycs-source-sticky",
            ),
            # The picker container — JS (picker.js) renders the table,
            # filter, Load-more, and bottom action bar into this DOM
            # once the fetch succeeds. Empty + hidden by default.
            Div(
                id  = "ycs-channel-picker",
                cls = "ycs-picker",
                data_state = "empty",
            ),
            # Sticky action bar — transcript options + Start Ingestion
            # docked to the viewport bottom while the picker scrolls.
            # Status div lives INSIDE the sticky bar so dispatch errors
            # surface right next to the button that triggered them.
            # Button is enabled by picker.js only when selection ≥ 1.
            _StickyOptionsBar(
                "channel",
                Button(
                    "Start Ingestion",
                    type = "submit",
                    cls  = "btn-primary",
                    disabled = True,
                    id = "ycs-channel-submit",
                    formnovalidate = True,  # picker submit, not fetch
                ),
                status_id = "ycs-channel-status",
                extra_actions = (
                    Button(
                        "Ingest all",
                        type = "button",
                        cls  = "ycs-sticky-bar-ingest-all",
                        disabled = True,
                        id = "ycs-channel-ingest-all",
                        title = (
                            "Queue every video in the channel, bypassing the "
                            "100-per-page picker cap."
                        ),
                    ),
                ),
            ),
            id = "ycs-channel-form",
        ),
        cls = "ycs-tab-body",
        id  = "ycs-tab-channel",
        role = "tabpanel",
    )
