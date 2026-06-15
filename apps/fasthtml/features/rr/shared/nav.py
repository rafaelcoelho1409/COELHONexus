"""Stage sub-nav for Research Radar (row 2 of the chrome).

Each tab is a real `<a href>` — no JS stepper. The active scan_id (if any)
threads through via `?scan=<uuid>` so flipping between Pipeline and Digest
keeps the in-flight scan attached without re-resolving from local state.

main.js auto-navigates Pipeline → Digest the moment the SSE `phase=done`
frame arrives via the same href shape this nav emits."""
from fasthtml.common import A, Nav

from .urls import _STAGES, stage_url


def RRStageSubNav(active_key: str, scan_id: str | None = None):
    """Render the row-2 tab strip. Reuses DD's `.dd-substage` styling so
    the three feature families share one chrome look."""
    links = []
    for key, label, _ in _STAGES:
        cls = "dd-substage active" if key == active_key else "dd-substage"
        links.append(A(label, href = stage_url(key, scan_id), cls = cls,
                       data_substage = key))
    return Nav(*links, cls = "dd-substage-nav",
               aria_label = "Research Radar stages")
