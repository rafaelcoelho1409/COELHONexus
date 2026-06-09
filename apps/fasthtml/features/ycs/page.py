"""YCSPage — wrap a stage body in shared chrome + the main.js entry.

`active_stage` is exposed on the root div as `data-ycs-stage` so main.js
branches its init sequence without re-parsing window.location.

Stage navigation lives in the shell's row 2 (`StageSubNav`, wired in
routes.py) — same place as Docs Distiller. The page body holds the
stage content only.

No `.fw-layout` wrapper here — that's DD's two-column flex (sidebar +
content) and YCS has no sidebar. Wrapping in it shrank `.ycs-root` to
its content width and left a gap on the right. `.ycs-page` is the
sole outer; `.ycs-root` is the column flex for the body's children."""
from __future__ import annotations

from fasthtml.common import Div, Script

from features.dd.shared.overlays import ConfirmModal

from .shared.pipeline_panel import PipelinePanel


def YCSPage(active_stage: str, slug: str | None, body):
    """Shared YCS page chrome.

    The pipeline panel is rendered ONLY on the Ingest page (the stage
    where it conceptually belongs — Source is for picking videos, Ask
    is for querying, Ingest is for watching the pipeline). Source and
    Ask never include the panel DOM at all, so there's nothing to
    leak. `pipeline_panel.js` is still loaded shell-wide by `main.js`
    so the boot's `document.getElementById("ycs-pipe-panel")` returns
    null on non-Ingest pages and the tracker no-ops.

    Persistence behavior on Ingest:
      - URL `?extract=&qdrant=&neo4j=` (Videos tab redirect) →
        persists to localStorage + starts tracking.
      - localStorage `ycs:pipeline:active` (24h TTL) → restores on
        page reload / navigation back to Ingest.
      - Stop click clears localStorage so a subsequent visit to
        Ingest doesn't resurface a cancelled run.

    `ConfirmModal()` is reused from DD's shared overlays — it's a
    framework-level confirm dialog driven by `showConfirm()` in
    `@dd/shared/ui/overlays.js`. CSS lives in `components/overlays.css`
    (shell-wide, not DD-scoped), so styling works anywhere. YCS's
    `pipeline_panel.js` calls `showConfirm()` for Stop confirmation in
    place of `window.confirm()`."""
    body_children = [body]
    if active_stage == "ingestion":
        body_children.insert(0, PipelinePanel())
    return Div(
        Div(
            *body_children,
            cls = "ycs-root",
        ),
        ConfirmModal(),
        Script(src = "/static/js/ycs/main.js", type = "module"),
        cls = "ycs-page",
        data_ycs_stage = active_stage,
        data_ycs_slug = (slug or ""),
    )
