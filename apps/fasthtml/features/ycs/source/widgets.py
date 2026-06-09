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


def _TranscriptOptions(prefix: str, *extra):
    """Reusable `include_transcription` + `transcription_languages` row.
    `prefix` namespaces the field IDs so the same fragment can mount on
    Videos / Channel / Playlist tabs without ID collisions.

    `*extra` trailing children get appended as additional grid cells —
    used by Videos to place the `Start Ingestion` submit button as the
    third column. When present, the grid switches to 3-column layout
    via the `.ycs-trans-opts-with-action` modifier class."""
    modifier = " ycs-trans-opts-with-action" if extra else ""
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
        *extra,
        cls = f"ycs-trans-opts{modifier}",
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
    """DEPRECATED 2026-06-08 — replaced by `_StickyOptionsBar`.

    Folded secondary form options behind a `Options ▸` disclosure at
    the bottom of the form. With Channel/Playlist tab pickers
    surfacing hundreds of rows, the user had to scroll past the entire
    picker to reach this control. The sticky-bottom pattern fixes that
    by docking the same controls to the viewport bottom while the
    picker scrolls.

    Kept here only so external callers (if any) still import — every
    in-repo tab module has been migrated."""
    return Details(
        Summary("Options", cls = "ycs-options-summary"),
        Div(*children, cls = "ycs-options-body"),
        cls = "ycs-options-collapse",
        id  = f"ycs-{prefix}-options",
        open = True,
    )


def _InfoPopover(text: str, *, label: str = "More info"):
    """Compact (ⓘ) icon button that toggles a popover with help text.

    Replaces the prior `<P>` instruction paragraph at the top of each
    Source tab — gives the same content but lets the URL/Fetch row
    breathe. Uses native `<details>` so the disclosure is keyboard-
    accessible without JS (Space/Enter on the summary toggles; Esc
    + focus-out close it via CSS `:not(:focus-within)`).

    Popover positions ABOVE the row via CSS — see
    `.ycs-info-popover` / `.ycs-info-content` in ycs.css."""
    return Details(
        Summary(
            "ⓘ",
            cls        = "ycs-info-btn",
            aria_label = label,
            title      = "Show instructions",
        ),
        Div(text, cls = "ycs-info-content", role = "tooltip"),
        cls = "ycs-info-popover",
    )


def _StickyOptionsBar(
    prefix:        str,
    submit_btn,
    status_id:     str | None = None,
    extra_actions: tuple = (),
):
    """Sticky-bottom action bar for the Source tabs — replaces
    `_OptionsCollapse` (2026-06-08 redesign).

    Pins to the viewport bottom via `position: sticky; bottom: 0;` so
    the primary action (Start Ingestion) stays one click away even
    when the user is mid-scroll through 100+ picker rows. SOTA shape
    per parallel WebSearches: Tim Graf 2026 UX / SEB Design Library /
    contentsquare all converge on the sticky-bar pattern for "primary
    CTA must stay visible while user scrolls a long list" — same idiom
    Gmail, Linear, Notion, Vercel use for bulk actions.

    Layout (single row):
      [ ☑ Fetch transcripts ]  [ Languages: en,pt,es ]  · · ·
      [<extra_actions…>]  [Submit]

    `status_id` (optional): if provided, inserts an inline status
    placeholder between the langs field and the submit button. Tab
    modules use this to surface dispatch errors right next to the
    button that triggered them.

    `extra_actions` (optional): secondary action elements inserted
    BEFORE the primary submit button. Used by Channel + Playlist tabs
    to dock the `Ingest all <N>` button right next to `Start Ingestion`
    so the two-mode choice (selection vs. all) is one glance."""
    children = [
        Div(
            Input(
                type    = "checkbox",
                name    = "include_transcription",
                id      = f"ycs-{prefix}-incl-trans",
                checked = True,
            ),
            Label(
                "Fetch transcripts",
                for_ = f"ycs-{prefix}-incl-trans",
                cls  = "ycs-filter-check-label",
            ),
            cls = "ycs-sticky-bar-check",
        ),
        Div(
            Label(
                "Languages",
                for_ = f"ycs-{prefix}-langs",
                cls  = "ycs-sticky-bar-label",
            ),
            Input(
                type = "text",
                name = "transcription_languages",
                id   = f"ycs-{prefix}-langs",
                placeholder = "en, pt, es",
                cls  = "ycs-sticky-bar-input",
            ),
            cls = "ycs-sticky-bar-langs",
        ),
    ]
    if status_id:
        children.append(Div(
            "", id = status_id, cls = "ycs-search-status ycs-sticky-bar-status",
        ))
    for action in extra_actions:
        children.append(action)
    children.append(submit_btn)
    return Div(
        Div(*children, cls = "ycs-sticky-bar-inner"),
        cls = "ycs-sticky-bar",
        id  = f"ycs-{prefix}-sticky",
    )
