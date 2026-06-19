"""Composer for `/` — assembles all sections into one Div tree.

2026-06-18 SOTA refactor: stats strip now reads all three product
backends (DD library, YCS videos, RR scans), not just DD. Each fetch
has its own 60s TTL cache (see cache.py) and fails soft to a "—"
placeholder rendered by Stats(), so a transient backend hiccup never
breaks the home page."""
from fasthtml.common import Div

from .cache import fetch_library, fetch_rr_total, fetch_ycs_total
from .sections import Features, Foot, Hero, HowItWorks, Stats


def Home():
    library   = fetch_library()
    ycs_total = fetch_ycs_total()
    rr_total  = fetch_rr_total()
    return Div(
        Hero(has_library = bool(library)),
        Stats(library, ycs_total, rr_total),
        Features(),
        HowItWorks(),
        Foot(),
        cls = "home-root",
    )
