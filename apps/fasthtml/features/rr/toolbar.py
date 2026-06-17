"""Row 3 — per-stage contextual chrome for Research Radar.

Two builders, each scoped to one stage:

  PipelineToolbar()  — scan form (Topic · Verticals · Deep reads · Start · Stop).
                       Lives ONLY on /research-radar so submitting a scan from
                       the digest reader can't accidentally happen.

  DigestToolbar()    — Recent scans dropdown. Lives ONLY on /research-radar/digest
                       so the picker shows up where you actually pick a scan
                       to read.

The split matches the page semantics: Pipeline is the "live operations"
surface (you start/stop runs here), Digest is the "read mode" surface
(you pick which past scan to read here). Mixing both controls across
both pages was a leftover from when there was a single toolbar shared
across stages."""
from __future__ import annotations

from fasthtml.common import Div, Span

from .body import ScanForm
from .shared.scans_picker import RRRecentScansPicker


def PipelineToolbar():
    """Row-3 chrome for `/research-radar`. Scan form + Recent-scans picker
    clustered in `.rr-actions` (2026-06-17 update).

    Why the picker is also here now: execution telemetry (LLM counters,
    per-node tokens, retry visualization, totals strip) all live on the
    Pipeline graph + drawer surfaces, NOT on Digest. Operators reviewing
    a past scan's cost / behavior need to load it INTO the Pipeline
    view; making them detour through Digest just to pick the scan was
    pointless friction.

    Mount path: passed via `ScanForm(extra_actions=…)` so the picker lands
    INSIDE the `.rr-actions` cluster next to Start/Stop — matches the CSS
    rule `.rr-actions .rr-scans-picker { flex: 0 0 auto; }` (rr.css:871),
    which was preserved from the historical shared-toolbar layout.

    The picker's row clicks are intercepted on Pipeline page (main.js)
    so clicking a past scan resumes it in-place via `resumeScan` instead
    of navigating to `/digest?scan=...`. That keeps the operator on the
    page they need to see the telemetry on.
    """
    return Div(
        ScanForm(extra_actions = RRRecentScansPicker()),
        cls = "dd-toolbar topbar-collapsible rr-toolbar rr-toolbar-pipeline",
        id  = "rr-toolbar",
    )


def DigestToolbar():
    """Row-3 chrome for `/research-radar/digest`. Scan topic on the left,
    Recent-scans dropdown on the right (2026-06-17 update).

    Why topic is here: lifted out of the digest body — operators reading
    findings need to know WHICH scan they're reading at a glance, and
    the toolbar row is sticky / collapsible across both stages so the
    context follows the reader as they scroll the findings list.

    Empty when no scan is loaded (data-empty="true" hides via CSS).
    main.js's resumeScan() fills `#rr-digest-topic` from the scan
    record on load."""
    return Div(
        Span(
            "",
            id    = "rr-digest-topic",
            cls   = "rr-digest-topic",
            title = "Scan topic",
            **{"data-empty": "true"},
        ),
        RRRecentScansPicker(),
        cls = "dd-toolbar topbar-collapsible rr-toolbar rr-toolbar-digest",
        id  = "rr-toolbar",
    )
