"""Non-feature pages: placeholder + health probe."""
from starlette.responses import PlainTextResponse

from layout.shell import _Shell


def register(rt) -> None:
    @rt("/coming-soon")
    def coming_soon():
        return _Shell("coming-soon", "Coming Soon")

    @rt("/health")
    def health():
        return PlainTextResponse("OK")
