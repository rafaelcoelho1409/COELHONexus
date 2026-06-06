"""Row 3 — contextual stage tools for the YCS wizard.

Mirrors `features/dd/shared/toolbar.py`: a `.dd-toolbar` (shared CSS
with Docs Distiller) carrying per-stage chrome on the left. The
right side will hold the library-picker dropdown once Slice 2 of
the YCS port introduces a library identifier (`?slug=`). Until then
the right cluster stays empty.

Returns `None` when the active stage has nothing to put in row 3 —
that lets `routes.py` pass it straight through to `_Shell`, which
renders an empty string when `toolbar_row` is None (= no row 3 at
all, no thin empty bar).
"""
from __future__ import annotations

from fasthtml.common import Div

from ..source.chrome import SourceModeTabs


def StageToolbar(active_stage: str, slug: str | None):
    if active_stage == "source":
        left = [SourceModeTabs("search")]
    else:
        return None
    return Div(
        Div(*left, cls = "dd-toolbar-left"),
        cls = "dd-toolbar topbar-collapsible",
        id = "ycs-toolbar",
    )
