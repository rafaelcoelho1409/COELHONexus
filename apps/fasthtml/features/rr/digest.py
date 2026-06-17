"""Digest page — reader view for `/research-radar/digest`.

Server returns a static shell; main.js's existing `renderDigest()` + the
`?scan=<id>` URL-state path do all the heavy lifting on the client. If
`?scan=` is missing on a fresh visit, the empty-state copy points the
operator at the toolbar form (which is still mounted on row 3 of the
chrome — the form is global across both pages)."""
from fasthtml.common import (
    Button, Div, P, Script, Span,
)

# Reuse DD's shared modal — same pattern as `features/ycs/page.py`. Mounts
# the `#fw-modal` element backed by `showConfirm()` in
# `static/js/dd/shared/ui/overlays.js`.
from features.dd.shared.overlays import ConfirmModal


def DigestBody():
    """Page body for `/research-radar/digest`.

    2026-06-17 SOTA visualization refactor:
      - Themes strip (sticky) + summary at the top — scan-wide context
      - Vertical ranked card list — each card is L0 always-visible with
        money_angle as the headline accent (the only structured field
        that earns L0 real estate); problem on hover (L1); full 5-field
        extraction in a side drawer on click (L2)
      - Side drawer replaces modal-style "show more" — preserves list
        context for browse/compare flows (2026 UX consensus)
      - Empty-state hint stays for the no-scan-loaded case
      - ConfirmModal stays for the scans-picker delete flow

    main.js hydrates from `?scan=<id>` on load + SSE `phase=done`."""
    return Div(
        # 2026-06-17 v3: topic now renders as a page-title heading
        # (red left-bar accent), not a pill. See pipeline.py for the
        # full rationale. Element id `#rr-digest-topic` preserved so
        # `_setPillTopic` still resolves it.
        Div(
            Span(
                "",
                id    = "rr-digest-topic",
                cls   = "rr-topic-title",
                title = "Scan topic",
                **{"data-empty": "true"},
            ),
            cls = "rr-topic-strip",
        ),
        # Empty-state hint (visible when no scan loaded; main.js hides
        # it when findings arrive).
        P(
            "Findings appear here once a scan completes. "
            "Start one from the form above; this page will auto-render the result.",
            id  = "rr-digest-empty",
            cls = "rr-digest-empty",
        ),
        # Scan-wide synthesis strip — themes (clickable chip filters)
        # + executive summary (collapsed by default; click to expand).
        # Filled by main.js renderDigest() from ScanResult.synthesis_*.
        Div(
            Div(
                Span("Themes", cls = "rr-synthesis-label"),
                Div(id = "rr-synthesis-themes", cls = "rr-synthesis-themes"),
                cls = "rr-synthesis-row",
            ),
            P("", id = "rr-synthesis-summary", cls = "rr-synthesis-summary"),
            id     = "rr-synthesis-strip",
            cls    = "rr-synthesis-strip",
            hidden = True,
        ),
        # Ranked card list — L0 cards rendered by renderDigest().
        Div(id = "rr-digest-items", cls = "rr-digest-items"),
        # Side drawer (L2) — slides in from right on card click.
        # Reused for every card; renderDigest() swaps content on open.
        Div(
            Div(
                Span("", id = "rr-finding-drawer-rank", cls = "rr-finding-drawer-rank"),
                Span("", id = "rr-finding-drawer-arxiv", cls = "rr-finding-drawer-arxiv"),
                Button(
                    "×",
                    type = "button",
                    cls  = "rr-finding-drawer-close",
                    id   = "rr-finding-drawer-close-btn",
                    **{"aria-label": "Close drawer"},
                ),
                cls = "rr-finding-drawer-head",
            ),
            Div(id = "rr-finding-drawer-body", cls = "rr-finding-drawer-body"),
            id     = "rr-finding-drawer",
            cls    = "rr-finding-drawer",
            hidden = True,
        ),
        ConfirmModal(),
        Script(src = "/static/js/rr/main.js", type = "module"),
        cls = "rr-page rr-page-digest",
    )
