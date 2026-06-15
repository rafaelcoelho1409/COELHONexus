"""Digest page — reader view for `/research-radar/digest`.

Server returns a static shell; main.js's existing `renderDigest()` + the
`?scan=<id>` URL-state path do all the heavy lifting on the client. If
`?scan=` is missing on a fresh visit, the empty-state copy points the
operator at the toolbar form (which is still mounted on row 3 of the
chrome — the form is global across both pages)."""
from fasthtml.common import (
    H3, Div, P, Script,
)


def _DigestArea():
    return Div(
        H3("Digest", cls = "rr-digest-title"),
        P(
            "Findings appear here once a scan completes. "
            "Start one from the form above; this page will auto-render the result.",
            id  = "rr-digest-empty",
            cls = "rr-digest-empty",
        ),
        Div(id = "rr-digest-items", cls = "rr-digest-items"),
        cls = "rr-digest",
    )


def DigestBody():
    """Page body for `/research-radar/digest`. The Pipeline page's status
    strip is omitted here — the operator already saw the run, this is the
    reading surface. main.js hydrates the digest items from `?scan=<id>`
    on load and intercepts SSE `phase=done` to swap in the live result."""
    return Div(
        Div(
            _DigestArea(),
            cls = "rr-card rr-card-digest",
        ),
        Script(src = "/static/js/rr/main.js", type = "module"),
        cls = "rr-page rr-page-digest",
    )
