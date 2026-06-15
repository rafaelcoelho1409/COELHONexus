"""Row 3 — contextual chrome for Research Radar.

Mirrors `features/dd/shared/toolbar.py` and `features/ycs/shared/toolbar.py`:
returns a `.dd-toolbar topbar-collapsible` `<div>` that the layout shell
mounts as the third sticky row above the page body.

For RR the chrome is the scan form itself — Topic / Verticals / Deep reads
on the left, Start Scan submit and the Recent-scans picker on the right.
Lifting the form out of the body reclaims ~200 px of vertical space for
the digest and matches the per-feature stage-tools idiom DD and YCS
already established."""
from __future__ import annotations

from fasthtml.common import Div

from .body import ScanForm
from .shared.scans_picker import RRRecentScansPicker


def ScanToolbar():
    """Return the row-3 toolbar div. `.dd-toolbar` styles + the per-row
    `topbar-collapsible` class let topbar.js auto-hide on scroll-down
    exactly like DD's and YCS's row-3 chrome.

    Recent-scans picker is passed into `ScanForm` as `extra_actions` so it
    lands inside the same `.rr-actions` cluster as Start/Stop — they read
    as one right-side action group."""
    return Div(
        ScanForm(extra_actions=RRRecentScansPicker()),
        cls = "dd-toolbar topbar-collapsible rr-toolbar",
        id  = "rr-toolbar",
    )
