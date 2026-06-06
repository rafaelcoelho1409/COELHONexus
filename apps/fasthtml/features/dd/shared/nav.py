"""Stage sub-nav. Each tab is a real <a href> (NO JS stepper) so every
stage is its own bookmarkable URL. `stage_url()` centralizes the
`?slug=` concat so users keep the active framework when jumping stages."""
from fasthtml.common import A, Nav

from .urls import _STAGES, stage_url


def StageSubNav(active_key: str, slug: str | None):
    links = []
    for key, label, _ in _STAGES:
        cls = "dd-substage active" if key == active_key else "dd-substage"
        links.append(A(label, href = stage_url(key, slug), cls = cls,
                       data_substage = key))
    return Nav(*links, cls = "dd-substage-nav",
               aria_label = "Docs Distiller stages")
