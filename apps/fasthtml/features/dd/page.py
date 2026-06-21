"""DDPage — wrap a stage body in shared chrome + overlays + the main.js entry.

`active_stage` is exposed on the root div as `data-dd-stage` so main.js
branches its init sequence without re-parsing window.location."""
from fasthtml.common import Div, Script

from .shared.overlays import (
    ConfirmModal, FileDrawer, LlmUsageDrawer, NodeDrawer, NoticeAndToast,
)
from .shared.sticky import StickyBar


def DDPage(active_stage: str, slug: str | None, body, with_sticky: bool = False):
    notice, toast = NoticeAndToast()
    extras = []
    if with_sticky:
        extras.append(StickyBar())
    return Div(
        Div(
            Div(
                notice,
                toast,
                body,
                cls = "fw-main",
            ),
            cls = "fw-layout",
        ),
        *extras,
        ConfirmModal(),
        FileDrawer(),
        NodeDrawer(),
        LlmUsageDrawer(),
        Script(src = "/static/js/dd/main.js", type = "module"),
        cls = "fw-picker",
        data_dd_stage = active_stage,
        data_dd_slug = (slug or ""),
    )
