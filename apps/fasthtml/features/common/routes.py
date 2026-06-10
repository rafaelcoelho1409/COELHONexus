"""Non-feature pages: placeholder + health probe."""
from starlette.responses import PlainTextResponse

from layout.shell import _Shell


def register(rt) -> None:
    @rt("/research-radar")
    def research_radar():
        return _Shell("research-radar", "Research Radar")

    @rt("/health")
    def health():
        return PlainTextResponse("OK")
