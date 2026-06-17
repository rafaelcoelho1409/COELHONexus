"""Digest page — reader view for `/research-radar/digest`.

Server returns a static shell; main.js's existing `renderDigest()` + the
`?scan=<id>` URL-state path do all the heavy lifting on the client. If
`?scan=` is missing on a fresh visit, the empty-state copy points the
operator at the toolbar form (which is still mounted on row 3 of the
chrome — the form is global across both pages)."""
from fasthtml.common import (
    Div, P, Script,
)

# Reuse DD's shared modal — same pattern as `features/ycs/page.py`. Mounts
# the `#fw-modal` element backed by `showConfirm()` in
# `static/js/dd/shared/ui/overlays.js`.
from features.dd.shared.overlays import ConfirmModal


def DigestBody():
    """Page body for `/research-radar/digest`.

    2026-06-17 restructure:
      - Container card (.rr-card-digest) removed — findings render
        directly on the page surface for a cleaner reading view
      - Title (H3 "Digest") + scan-topic display lifted into the row-3
        toolbar (see DigestToolbar in toolbar.py). The toolbar is sticky,
        so the topic stays visible as the operator scrolls the findings
      - This page is now just: empty-state hint + findings list +
        ConfirmModal target for the scans-picker delete flow

    main.js hydrates the items from `?scan=<id>` on load and intercepts
    SSE `phase=done` to swap in the live result."""
    return Div(
        P(
            "Findings appear here once a scan completes. "
            "Start one from the form above; this page will auto-render the result.",
            id  = "rr-digest-empty",
            cls = "rr-digest-empty",
        ),
        Div(id = "rr-digest-items", cls = "rr-digest-items"),
        ConfirmModal(),
        Script(src = "/static/js/rr/main.js", type = "module"),
        cls = "rr-page rr-page-digest",
    )
