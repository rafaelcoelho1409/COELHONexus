"""YCS stage sub-nav — row-2 tab strip pinned in the shell topbar.

Mirrors `features/dd/shared/nav.py`: each stage is a real `<a href>`,
the active key gets `.active`. CSS classes (`dd-substage-nav`/
`dd-substage`) are shared with DD on purpose — the look is identical
by design, and reusing the selector guarantees pixel-parity if DD
ever retouches the row-2 styling. The `dd-` prefix is naming history,
not a feature gate."""
from __future__ import annotations

from fasthtml.common import A, Nav

from .urls import _STAGES, stage_url


def StageSubNav(active_key: str, slug: str | None):
    links = []
    for key, label, _ in _STAGES:
        cls = "dd-substage active" if key == active_key else "dd-substage"
        links.append(A(label, href = stage_url(key, slug), cls = cls,
                       data_substage = key))
    return Nav(*links, cls = "dd-substage-nav",
               aria_label = "YouTube Content Search stages")
