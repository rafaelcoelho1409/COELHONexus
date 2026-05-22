"""YouTube Content Search — Coming Soon placeholder."""
from fasthtml.common import Div, H2, P

from shell import _Shell


def register(rt) -> None:
    @rt("/youtube-content-search")
    def youtube_content_search():
        return _Shell(
            "youtube-content-search",
            "YouTube Content Search",
            body=Div(
                H2("Coming Soon", style="color: var(--text-muted); font-weight: 400;"),
                P(
                    "YouTube Content Search is under development. "
                    "Check back once Docs Distiller is fully functional.",
                    style="color: var(--text-muted);",
                ),
                style="text-align: center; padding: 120px 0;",
            ),
        )
