"""Row-3 chrome for the Ask stage — mode pills + thread bar (left) +
LLM picker (right).

Three pieces:

  * `AskModeTabs(active="")` — Auto / Fast / Standard / Deep pills. The
    DOM contract `[data-mode]` + `.ycs-mode-pill` is preserved so
    `ask.js` keeps binding without changes; the additional
    `.dd-substage` class gives them the same row-3 visual language as
    the Source-stage tabs (Search | Videos | Playlist | Channel).

  * `AskThreadBar()` — current thread id badge + New thread button.
    DOM ids (`#ycs-ask-thread-id`, `#ycs-ask-new-thread`) match what
    `ask.js` already binds; only their position moved.

  (2026-06-17) The `Model: Auto` rotator-status trigger was removed —
  it advertised information the user couldn't act on. The rotator's
  per-call arm is now surfaced in-line on the response itself; the
  topbar real estate goes back to the more actionable +New thread /
  Thread picker / Scope controls."""
from __future__ import annotations

from fasthtml.common import (
    Button, Div, Nav, Span,
)


_MODES: list[tuple[str, str]] = [
    ("",         "Auto"),
    ("fast",     "Fast"),
    ("standard", "Standard"),
    ("deep",     "Deep"),
]


def AskModeTabs(active: str = ""):
    """Auto | Fast | Standard | Deep — row-3 left cluster. The Auto pill
    (data-mode="") is the default `active` so first-load matches the
    server-side `force_mode = None` behaviour."""
    pills = [
        Button(
            label,
            type      = "button",
            cls       = ("ycs-mode-pill dd-substage active"
                         if key == active else
                         "ycs-mode-pill dd-substage"),
            data_mode = key,
        )
        for key, label in _MODES
    ]
    return Nav(
        *pills,
        cls         = "dd-substage-nav ycs-ask-modes",
        role        = "tablist",
        aria_label  = "Ask mode",
    )


def AskScopeTrigger():
    """Channel-scope picker — row-3 left cluster, sits next to the mode
    tabs. Same `.dd-catfilter` trigger+popover idiom as the LLM /
    Thread pickers; the popover hosts a checkbox row per indexed
    channel that `ask.js` populates from `GET /admin/ingested-channels`.

    Trigger label tracks the current selection:
      - 0 channels chosen → "All channels"
      - 1 channel chosen  → that channel's name
      - N>1 chosen         → "N channels"

    DOM contract is consumed by `ask.js`:
      `#ycs-ask-scope-trigger`, `#ycs-ask-scope-label`, `#ycs-ask-scope-list`.
    Submit-time channel ids are read from the JS-internal `Set` rather
    than from the DOM — no hidden `<select>` to keep in sync."""
    return Div(
        Button(
            Span("Scope:", cls = "dd-catfilter-prefix"),
            Span("All channels",
                 cls = "dd-catfilter-label",
                 id  = "ycs-ask-scope-label"),
            Span("▾", cls = "dd-catfilter-chevron"),
            type       = "button",
            cls        = "dd-catfilter-trigger",
            id         = "ycs-ask-scope-trigger",
            aria_label = "Channel scope",
            title      = "Restrict retrieval to specific channels",
        ),
        Div(
            Div("",
                id  = "ycs-ask-scope-list",
                cls = "ycs-ask-scope-list"),
            cls = "dd-catfilter-popover ycs-ask-scope-popover",
        ),
        cls = "dd-catfilter ycs-ask-scope",
        id  = "ycs-ask-scope",
    )


def AskNewThreadButton():
    """Standalone `+ New thread` action — row-3 toolbar peer of the
    Thread dropdown (2026-06-15: moved OUT of the dropdown popover so
    the most common destructive-ish action is one click away, not two,
    and not hidden behind a chevron). DOM id `#ycs-ask-new-thread`
    matches what `ask.js`'s click handler already binds.

    Styled with `.dd-catfilter-trigger` so it matches the Thread / LLM
    dropdown triggers (and the Ingestion-stage filter pills) — same
    card background, border, padding, hover state. The trigger class
    has no chevron, so it reads visually as a flat action button while
    staying in the toolbar's typographic family."""
    return Button(
        "+ New thread",
        type  = "button",
        id    = "ycs-ask-new-thread",
        cls   = "dd-catfilter-trigger ycs-ask-new-thread-btn",
        title = "Start a fresh conversation",
    )


def AskThreadBar():
    """Thread picker — row-3 right cluster.

    `.dd-catfilter` dropdown: trigger shows the current thread id (set
    by `ask.js` on boot from `localStorage`); popover lists existing
    threads (`#ycs-ask-thread-list`, populated on open by
    `loadThreadList()` from `GET /agents/threads`). The `+ New thread`
    action is rendered separately by `AskNewThreadButton()` so it's
    visible without opening the dropdown — see that function for the
    rationale."""
    return Div(
        Button(
            Span("Thread:", cls = "dd-catfilter-prefix"),
            Span(
                "",
                cls   = "dd-catfilter-label ycs-ask-thread-id",
                id    = "ycs-ask-thread-id",
                title = "Conversation memory key (Postgres)",
            ),
            Span("▾", cls = "dd-catfilter-chevron"),
            type       = "button",
            cls        = "dd-catfilter-trigger",
            id         = "ycs-ask-thread-trigger",
            aria_label = "Switch thread",
        ),
        Div(
            Div(
                "",
                id  = "ycs-ask-thread-list",
                cls = "ycs-ask-thread-list",
            ),
            cls = "dd-catfilter-popover ycs-ask-thread-popover",
        ),
        cls = "dd-catfilter ycs-ask-thread",
        id  = "ycs-ask-thread",
    )


