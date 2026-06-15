"""Research Radar routes — two-page split (Pipeline / Digest).

Pattern mirrors `features/dd/routes.py` and `features/ycs/routes.py`:
  - Row 2 carries the stage sub-nav (`RRStageSubNav`).
  - Row 3 carries the shared `ScanToolbar` so the form is reachable on
    both pages without re-mounting.
  - Each page exposes its own `*Body()` for the main content surface.

Active scan_id is read from `?scan=` and threaded into the stage tabs +
the toolbar so a switch from Pipeline → Digest carries the live scan
without re-resolving from local state."""
from typing import Optional

from layout.shell import _Shell

from .digest   import DigestBody
from .pipeline import PipelineBody
from .shared.nav import RRStageSubNav
from .toolbar  import ScanToolbar


def _scan_id(request) -> Optional[str]:
    """Pull `?scan=<uuid>` off the request and forward it as-is. Validation
    is client-side (loose UUID-shape) and server-side (Pydantic on POST)."""
    raw = request.query_params.get("scan") if hasattr(request, "query_params") else None
    return raw or None


def register(rt) -> None:
    @rt("/research-radar")
    def research_radar_pipeline(request):
        sid = _scan_id(request)
        return _Shell(
            "research-radar",
            "Research Radar",
            body        = PipelineBody(),
            subnav_row  = RRStageSubNav("pipeline", sid),
            toolbar_row = ScanToolbar(),
        )

    @rt("/research-radar/digest")
    def research_radar_digest(request):
        sid = _scan_id(request)
        return _Shell(
            "research-radar",
            "Research Radar",
            body        = DigestBody(),
            subnav_row  = RRStageSubNav("digest", sid),
            toolbar_row = ScanToolbar(),
        )
