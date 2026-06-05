"""`/` — landing page."""
from layout.shell import _Shell

from .page import Home


def register(rt) -> None:
    @rt("/")
    def index():
        # active_key = "home" doesn't match any FEATURES → every nav link
        # renders inactive (correct on the home page). title_text = None
        # skips the burgundy-bordered H1 row so the hero sits flush.
        return _Shell("home", title_text = None, body = Home())
