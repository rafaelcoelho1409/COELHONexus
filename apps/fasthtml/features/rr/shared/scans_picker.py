"""Recent-scans dropdown for the row-2 chrome.

Sits to the right of the Pipeline / Digest stage tabs. Mirrors the
verticals `<details>` pattern for the open/close affordance — no JS
needed for the toggle, just for populating the panel on first open via
`GET /api/v1/rr/scans/recent`.

The picker is intentionally simple — one row per scan, click → navigate
to its digest. No filters or pagination today (the operator's 20 most
recent scans are enough for a single-user setup)."""
from fasthtml.common import (
    Button, Details, Div, NotStr, Span, Summary,
)


def RRRecentScansPicker():
    """Empty-state shell — `main.js::loadRecentScans()` fills `#rr-scans-list`
    on first dropdown open via the API endpoint."""
    return Details(
        Summary(
            Span("Recent scans", cls = "rr-scans-picker-label"),
            Span("▾", cls = "rr-scans-picker-caret", **{"aria-hidden": "true"}),
            cls = "rr-scans-picker-summary",
        ),
        Div(
            Div(
                NotStr("Loading…"),
                id  = "rr-scans-list",
                cls = "rr-scans-list",
            ),
            cls = "rr-scans-picker-panel",
        ),
        id  = "rr-scans-picker",
        cls = "rr-scans-picker",
    )
