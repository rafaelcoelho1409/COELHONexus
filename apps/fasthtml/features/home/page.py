"""Composer for `/` — assembles all sections into one Div tree."""
from fasthtml.common import Div

from .cache import fetch_library
from .sections import Features, Foot, Hero, HowItWorks, Stats


def Home():
    library = fetch_library()
    return Div(
        Hero(has_library = bool(library)),
        Stats(library),
        Features(),
        HowItWorks(),
        Foot(),
        cls = "home-root",
    )
