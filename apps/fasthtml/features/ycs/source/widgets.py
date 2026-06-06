"""Shared form widgets for the Source tabs — label + field wrapper,
the transcript-options row, the URL-preview unfurl card, and the
Options collapse.

Each tab module (`search.py`, `videos.py`, `channel.py`, `playlist.py`)
imports from here so the form-row vocabulary stays single-sourced. Same
split shape as `features/dd/catalog/widgets.py`."""
from __future__ import annotations

from fasthtml.common import Details, Div, Input, Label, Span, Summary


def _FilterField(name: str, label: str, *children):
    return Div(
        Label(label, cls = "ycs-filter-label", for_ = f"ycs-filter-{name}"),
        *children,
        cls = "ycs-filter-field",
    )


def _TranscriptOptions(prefix: str):
    """Reusable `include_transcription` + `transcription_languages` row.
    `prefix` namespaces the field IDs so the same fragment can mount on
    Videos / Channel / Playlist tabs without ID collisions."""
    return Div(
        Div(
            Input(
                type    = "checkbox",
                name    = "include_transcription",
                id      = f"ycs-{prefix}-incl-trans",
                checked = True,
            ),
            Label(
                "Fetch transcripts (Playwright)",
                for_ = f"ycs-{prefix}-incl-trans",
                cls  = "ycs-filter-check-label",
            ),
            cls = "ycs-filter-check-wrap",
        ),
        _FilterField(
            f"{prefix}-langs", "Languages (optional)",
            Input(
                type = "text",
                name = "transcription_languages",
                id   = f"ycs-{prefix}-langs",
                placeholder = "en, pt, es (comma-separated)",
                cls  = "ycs-filter-input",
            ),
        ),
        cls = "ycs-trans-opts",
    )


def _UrlPreview(prefix: str):
    """Placeholder for the client-side URL-parse preview card. Hidden
    by default; channel.js / playlist.js render the parsed display
    here when the user pastes a URL. The preview's `data-state` is
    one of `hidden | parsed | invalid` — the source.js consumer
    drives the state transitions.

    Mirrors the Slack / Linear / Notion link-unfurl idiom (SOTA per
    June 2026 research) but is **client-side only** — the actual
    metadata fetch happens server-side on submit, preserving the
    1:1 port commitment (no new backend probe endpoint)."""
    return Div(
        cls = "ycs-url-preview",
        id  = f"ycs-{prefix}-preview",
        data_state = "hidden",
    )


def _OptionsCollapse(*children, prefix: str):
    """Folds secondary form options (max_results, transcripts) behind a
    `Options ▸` disclosure. Open-by-default so the user doesn't have to
    click to discover them on first visit; collapse-by-default would
    hide the max_results affordance that channel/playlist needs.

    Uses native <details> so keyboard accessibility is free."""
    return Details(
        Summary("Options", cls = "ycs-options-summary"),
        Div(*children, cls = "ycs-options-body"),
        cls = "ycs-options-collapse",
        id  = f"ycs-{prefix}-options",
        open = True,
    )
