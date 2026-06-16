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

from fasthtml.common import Div

from .body import ScanForm
from .shared.scans_picker import RRRecentScansPicker


def PipelineToolbar():
    """Row-3 chrome for `/research-radar`. Scan form only — no picker
    here; the operator picks a scan to read on the Digest page."""
    return Div(
        ScanForm(),
        cls = "dd-toolbar topbar-collapsible rr-toolbar rr-toolbar-pipeline",
        id  = "rr-toolbar",
    )


def DigestToolbar():
    """Row-3 chrome for `/research-radar/digest`. Just the Recent-scans
    dropdown — no form here; new scans are launched from the Pipeline
    page where the operator can watch them run."""
    return Div(
        RRRecentScansPicker(),
        cls = "dd-toolbar topbar-collapsible rr-toolbar rr-toolbar-digest",
        id  = "rr-toolbar",
    )
