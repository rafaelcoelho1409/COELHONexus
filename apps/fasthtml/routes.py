"""Non-feature page routes: placeholder pages + health probe.

The Docs Distiller routes live in `features/docs_distiller.py` so they
can stay close to their `_Picker` component. Everything else is here.
"""
from starlette.responses import PlainTextResponse

from shell import _Shell


def register(rt) -> None:
    @rt("/youtube-content-search")
    def youtube_search():
        return _Shell("youtube-content-search", "YouTube Content Search")

    @rt("/coming-soon")
    def coming_soon():
        return _Shell("coming-soon", "Coming Soon")

    @rt("/health")
    def health():
        return PlainTextResponse("OK")
