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


def YCSPage(active_stage: str, slug: str | None, body):
    return Div(
        Div(
            body,
            cls = "ycs-root",
        ),
        Script(src = "/static/js/ycs/main.js", type = "module"),
        cls = "ycs-page",
        data_ycs_stage = active_stage,
        data_ycs_slug = (slug or ""),
    )
