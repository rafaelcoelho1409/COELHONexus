"""Non-feature pages: health probe.

(2026-06-12: /research-radar moved to features/rr/ — full page with scan
form + SSE progress + digest cards. The placeholder used to live here.)
"""
from starlette.responses import PlainTextResponse


def register(rt) -> None:
    @rt("/health")
    def health():
        return PlainTextResponse("OK")
