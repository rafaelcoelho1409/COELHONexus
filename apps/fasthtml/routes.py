"""Non-feature page routes: placeholder pages + health probe.

Feature pages live in their own `features/<name>.py` modules so they
can keep their helper components close. Everything else is here.
"""
from starlette.responses import PlainTextResponse

from shell import _Shell


def register(rt) -> None:
    @rt("/coming-soon")
    def coming_soon():
        return _Shell("coming-soon", "Coming Soon")

    @rt("/health")
    def health():
        return PlainTextResponse("OK")
